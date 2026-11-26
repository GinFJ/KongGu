"""Start a single-host Konggu account and batch service."""
import argparse
import os
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")


def main():
    parser = argparse.ArgumentParser(description="空谷账号与云端课表服务")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not os.environ.get("KONGGU_CLOUD_DATA"):
        parser.error("请通过 KONGGU_CLOUD_DATA 指定专用的持久化数据目录。")
    Path(os.environ["KONGGU_CLOUD_DATA"]).mkdir(parents=True, exist_ok=True, mode=0o700)

    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from waitress import serve
    from settings import MAX_BODY_BYTES

    application = get_wsgi_application()
    call_command("migrate", interactive=False, verbosity=0)
    proxy_options = {}
    if os.environ.get("KONGGU_CLOUD_HOST"):
        proxy_options = dict(trusted_proxy="127.0.0.1", trusted_proxy_count=1,
                             trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto"})
    print(f"空谷账号服务已启动，监听 127.0.0.1:{args.port}", flush=True)
    serve(application, host="127.0.0.1", port=args.port, threads=4,
          max_request_body_size=MAX_BODY_BYTES + 1, **proxy_options)


if __name__ == "__main__":
    main()
