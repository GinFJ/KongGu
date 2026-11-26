# 空谷账号与云端批次服务

这套服务由使用方自行托管。桌面端以账号登录，列出该账号保存的批次并读取或保存其中一批。服务只接受桌面端提交的批次 JSON。账号与批次归属均由服务端核实。数据目录包含成员课表与复核记录，应由服务管理员保管。

## 本机验证

Windows 上可双击本目录的 [start-local.cmd](start-local.cmd) 启动本机试用服务。需要已安装 Python 3.11 或 3.12，首次准备环境可能需要联网。打开空谷时填写 `http://127.0.0.1:8765`，保持服务窗口打开。关闭窗口后，账号与批次仍保存在本机数据目录，下次运行可继续使用。

使用 Python 3.11 或 3.12。在本目录打开 PowerShell，执行以下命令。首次安装 Python 包需要联网。Django 与 Waitress 只安装在服务端环境，空谷的离线识别和 Windows 安装包均不依赖它们。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe test_http.py -v

$env:KONGGU_CLOUD_DATA = Join-Path $env:LOCALAPPDATA 'KongguCloudService'
.\.venv\Scripts\python.exe server.py --port 8765
```

服务在启动时创建数据目录并执行数据库迁移，默认绑定本机 `127.0.0.1`。本机联调地址为 `http://127.0.0.1:8765`。在另一台电脑上使用空谷时，客户端应填写管理员提供的 HTTPS 域名。测试程序使用一次性临时库和合成账号。

## 对外提供 HTTPS 服务

以下单机部署适合小团队。选择管理员能持续维护的服务器和持久磁盘，创建专用低权限操作系统账号，把 `cloud_service` 目录复制到服务器。将域名解析至该主机，允许 Caddy 使用 80 和 443 端口。应用端口仅供本机反向代理访问。

在服务目录中创建 Python 虚拟环境并安装 `requirements.txt`。启动命令示例：

```sh
export KONGGU_CLOUD_DATA=/var/lib/konggu-cloud
export KONGGU_CLOUD_HOST=konggu.example.org
.venv/bin/python server.py --port 8765
```

将示例域名换成自己的域名，数据目录授予服务账号独占读写权限。将该命令交给操作系统服务管理器持续运行，配置自动重启。SQLite 数据文件位于持久目录的 `konggu.sqlite3`。单机部署仅运行一个 Waitress 进程，其 4 个工作线程共用限流计数。设置域名后，Waitress 仅信任本机反向代理转发的客户端地址。

Caddyfile 使用与 `KONGGU_CLOUD_HOST` 相同的域名：

```caddy
konggu.example.org {
    reverse_proxy 127.0.0.1:8765
}
```

Caddy 为域名申请和续签 TLS 证书。启用操作系统磁盘加密，按期将数据库备份到受限位置。冷备时先停止服务，再复制整个数据目录，恢复时使用同版本程序打开其副本。升级前保存旧程序与数据备份。维持默认的私密日志设置，避免记录认证请求正文、Authorization 头和课表正文。SQLite 存储的课表正文可被有磁盘读取权限的管理员读取。公开注册面向所有能访问此服务的人，可通过机构网络入口限制开放范围。 [Caddy 官方反向代理说明](https://caddyserver.com/docs/quick-starts/reverse-proxy) · [Waitress 官方代理参数](https://docs.pylonsproject.org/projects/waitress/en/stable/arguments.html)

## 账号与批次协议

请求正文使用 UTF-8 编码的 `application/json`。注册与登录响应返回 `token`、稳定随机 UUID `account_id` 和 `username`。后续请求使用 `Authorization: Bearer <token>`。令牌有效期为 7 天，退出立即注销当前令牌，其他设备可继续使用各自的会话。数据库保留令牌摘要。密码使用 Django 的密码散列与验证器，至少 10 个字符，不接受常见密码或纯数字密码。单个来源地址及单个账号名的登录与注册均有限流。 [Django 官方账号文档](https://docs.djangoproject.com/en/5.2/ref/contrib/auth/)

| 方法与路径 | 请求与结果 |
| --- | --- |
| POST `/v1/register` | 提交 `{username,password}`，创建账号并登录。 |
| POST `/v1/login` | 提交 `{username,password}`，创建新的登录会话。 |
| POST `/v1/logout` | 注销当前令牌。 |
| GET `/v1/batches` | 返回 `{batches:[{id,title,revision,updated_at}]}`，按最后保存时间倒序。 |
| GET `/v1/batches/{id}` | 返回该账号对应的批次信息及 `document`。 |
| PUT `/v1/batches/{id}` | 提交 `{title,base_revision,document}`。创建时使用客户端生成的 UUID 与版本 0，更新时携带已读取的版本。返回批次的最新摘要。 |

每份 `document` 为 JSON 对象，紧凑 UTF-8 编码最多 64 MiB，整个请求额外预留 64 KiB。名称最多 100 字符。服务端原子比较版本，两个设备同时保存时只有一个成功。版本冲突返回 409，客户端可重新下载或保存为独立批次。访问本账号下不存在的 UUID 返回 404。令牌失效返回 401，限流返回 429。日期按 UTC 保存，响应带时区。
