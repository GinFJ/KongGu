"""Bearer-only JSON API. Ownership always comes from a live server-side session."""
import hashlib
import json
import re
import secrets
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from .models import AccessToken, AccountIdentity, Batch


def reply(data, status=200):
    return JsonResponse(data, status=status, json_dumps_params={"ensure_ascii": False, "separators": (",", ":")})


class PrivateResponses:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.get_host()
        response = self.get_response(request)
        response["Cache-Control"] = "no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class ApiError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def endpoint(*methods, authenticated=True):
    def decorate(view):
        @wraps(view)
        def wrapped(request, **kwargs):
            try:
                if request.method not in methods:
                    response = reply({"error": "请求方式不支持。"}, 405)
                    response["Allow"] = ", ".join(methods)
                    return response
                if authenticated:
                    header = request.headers.get("Authorization", "")
                    match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{43})", header)
                    session = None
                    if match:
                        session = AccessToken.objects.filter(digest=digest(match[1]), expires_at__gt=timezone.now(),
                                                             owner__is_active=True).first()
                    if session is None:
                        raise ApiError(401, "登录已失效，请重新登录。")
                    request.cloud_session = session
                return view(request, **kwargs)
            except ApiError as error:
                response = reply({"error": error.message}, error.status)
                if error.status == 429:
                    response["Retry-After"] = "600"
                return response
            except RequestDataTooBig:
                return reply({"error": "云端批次体积超出限制。"}, 413)
        return wrapped
    return decorate


def json_body(request, limit):
    if request.content_type != "application/json":
        raise ApiError(415, "请使用 JSON 提交。")
    if int(request.META.get("CONTENT_LENGTH") or 0) > limit or len(request.body) > limit:
        raise ApiError(413, "请求体积超出限制。")
    try:
        value = json.loads(request.body.decode("utf-8"))
    except (ValueError, RecursionError):
        raise ApiError(400, "请求数据格式无效。") from None
    if not isinstance(value, dict):
        raise ApiError(400, "请求数据格式无效。")
    return value


def throttle(key, limit):
    # LocMemCache.add and incr are individually atomic; this service runs one Waitress process.
    cache_key = "attempt-" + digest(key)
    if cache.add(cache_key, 1, timeout=600):
        return
    try:
        attempts = cache.incr(cache_key)
    except ValueError:  # Expiration between the add and increment.
        return
    if attempts > limit:
        raise ApiError(429, "尝试次数较多，请在十分钟后重试。")


@sensitive_variables("password", "body")
def credentials(request, operation):
    throttle(f"{operation}:ip:{request.META.get('REMOTE_ADDR', '')}", 30)
    body = json_body(request, 4096)
    username = body.get("username")
    password = body.get("password")
    if not isinstance(username, str) or not isinstance(password, str) or not 1 <= len(password) <= 256:
        raise ApiError(400, "请填写有效的账号和密码。")
    username = get_user_model().normalize_username(username.strip())
    if not 1 <= len(username) <= 150:
        raise ApiError(400, "请填写有效的账号名。")
    try:
        username.encode("utf-8")
        password.encode("utf-8")
    except UnicodeEncodeError:
        raise ApiError(400, "账号或密码中有无效字符。") from None
    throttle(f"{operation}:name:{username}", 10)
    return username, password


@sensitive_variables("token")
def start_session(user, status=200):
    now = timezone.now()
    token = secrets.token_urlsafe(32)
    AccessToken.objects.filter(expires_at__lte=now).delete()
    AccessToken.objects.create(digest=digest(token), owner=user, expires_at=now + timedelta(days=7))
    identity, _ = AccountIdentity.objects.get_or_create(owner=user)
    return reply({"token": token, "account_id": str(identity.public_id), "username": user.username}, status)


@endpoint("POST", authenticated=False)
@sensitive_variables("password")
def register(request):
    username, password = credentials(request, "register")
    user = get_user_model()(username=username)
    try:
        user._meta.get_field("username").run_validators(username)
        validate_password(password, user=user)
    except ValidationError as error:
        raise ApiError(400, " ".join(error.messages)) from None
    user.set_password(password)
    try:
        with transaction.atomic():
            user.save(force_insert=True)
            return start_session(user, 201)
    except IntegrityError:
        raise ApiError(409, "该账号名无法注册，请换一个账号名。") from None


@endpoint("POST", authenticated=False)
@sensitive_variables("password")
def login(request):
    username, password = credentials(request, "login")
    user = authenticate(request, username=username, password=password)
    if user is None:
        raise ApiError(401, "账号或密码错误。")
    return start_session(user)


@endpoint("POST")
def logout(request):
    request.cloud_session.delete()
    return reply({"ok": True})


SUMMARY_FIELDS = ("batch_id", "title", "revision", "updated_at")


def summary(row):
    return {"id": str(row["batch_id"]), "title": row["title"], "revision": row["revision"],
            "updated_at": row["updated_at"].isoformat()}


@endpoint("GET")
def batches(request):
    rows = Batch.objects.filter(owner_id=request.cloud_session.owner_id).order_by("-updated_at").values(*SUMMARY_FIELDS)
    return reply({"batches": [summary(row) for row in rows]})


def prepare_batch(request):
    body = json_body(request, settings.MAX_BODY_BYTES)
    title, revision, document = (body.get(key) for key in ("title", "base_revision", "document"))
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
        raise ApiError(400, "请填写 1 至 100 个字符的批次名称。")
    if type(revision) is not int or not 0 <= revision < 2147483647 or not isinstance(document, dict):
        raise ApiError(400, "批次或版本格式无效。")
    try:
        serialized = json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        size = len(serialized.encode("utf-8"))
        title.encode("utf-8")
    except (ValueError, RecursionError):
        raise ApiError(400, "批次内容格式无效。") from None
    if size > settings.MAX_DOCUMENT_BYTES:
        raise ApiError(413, "单批数据超过 64 MiB。")
    return title.strip(), revision, serialized


@endpoint("GET", "PUT")
def batch(request, batch_id):
    owned = Batch.objects.filter(owner_id=request.cloud_session.owner_id, batch_id=batch_id)
    if request.method == "GET":
        row = owned.values(*SUMMARY_FIELDS, "document").first()
        if row is None:
            raise ApiError(404, "找不到这个批次。")
        return reply({**summary(row), "document": json.loads(row["document"])})

    title, revision, document = prepare_batch(request)
    now = timezone.now()
    updated = dict(title=title, revision=revision + 1, updated_at=now, document=document)
    if revision == 0:
        try:
            Batch.objects.create(owner_id=request.cloud_session.owner_id, batch_id=batch_id, **updated)
        except IntegrityError:
            raise ApiError(409, "云端已有该批次，请重新打开后再保存。") from None
    elif not owned.filter(revision=revision).update(**updated):
        if owned.exists():
            raise ApiError(409, "另一台设备已更新该批次，请重新打开云端版本。")
        raise ApiError(404, "找不到这个批次。")
    return reply(summary({"batch_id": batch_id, **updated}), 201 if revision == 0 else 200)
