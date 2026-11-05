# astrbot_plugin_config_backup

给 AstrBot 用的定时配置备份插件。把核心配置、所有插件的配置，以及（可选）对话数据库打包成 zip，按份数自动清理旧备份，支持管理员在聊天里手动触发。

写这个插件的原因很直接：AstrBot 的 WebUI「更新」是把新版本 pip 安装到**容器可写层**里的，一旦有人执行 `docker compose up -d` 重建容器，可写层整个丢失，版本回滚到镜像内的老版本；更糟的是老版本启动时会按自己的 schema 重写 `cmd_config.json`，把新版本才有的配置项删掉，你的模型选择、人设、最大步数、搜索 Key 等设置会在你不知情的情况下回到默认值。

有了定时备份，至少这些设置可以找回来。

## 功能

- 定时打包 `data/cmd_config.json` 与 `data/config/*.json`（每个插件的独立配置）
- 可选把 `data_v4.db`（对话记录、记忆库）一起备份，使用 SQLite 官方在线备份接口，不会拷到写了一半的库
- 原子写入：先写 `.zip.tmp`，完成后再改名，中途失败不会留下半个包
- 按份数轮转，自动删除最旧的备份
- 启动 30 秒后先备份一次，避免等第一个周期
- 管理员指令：立即备份 / 查看备份列表
- 所有配置都能在 WebUI 插件配置页里改，无需改代码

## 安装

把整个目录放进 AstrBot 数据目录下的 `plugins`：

```
<AstrBot 数据目录>/plugins/astrbot_plugin_config_backup/
```

下文用 `$ASTRBOT_DATA` 指代你的数据目录。常见 Docker 部署下它是 `data` 卷挂载出来的目录，例如 `/vol1/1000/docker/astrbot/data`，但请以自己的 `docker-compose.yml` 挂载为准。

或者在 WebUI 的「插件」页面里从仓库地址安装。放好后重启 AstrBot（`docker restart <容器名>`，不要 `docker compose up -d`），插件会自动加载。下文用 `$CONTAINER` 指代你的 AstrBot 容器名，默认示例为 `astrbot`。


依赖：仅使用 Python 标准库（`zipfile`、`sqlite3`、`asyncio`），无需 `pip install`。

## 配置

WebUI → 插件 → astrbot_plugin_config_backup → 配置：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| 启用插件 | bool | `true` | 总开关 |
| 备份间隔（小时） | float | `6` | 最小 0.05（3 分钟）。改完从下一轮开始重新计时 |
| 保留份数 | int | `30` | 只保留最近 N 份，超出的按修改时间删除 |
| 启动时先备份一次 | bool | `true` | 加载插件 30 秒后立刻备份 |
| 同时备份数据库 | bool | `false` | 勾选后把 `data_v4.db` 一起打包 |
| 备份文件范围 | list | `["cmd_config.json", "config/*.json"]` | 相对 `data` 目录的 glob 模式 |

也可以通过直接编辑 `data/config/astrbot_plugin_config_backup_config.json` 修改，改完需要重启 AstrBot。

### 关于体积

`data_v4.db` 原生约 50 MB，压缩后约 20 MB。如果开启数据库备份并保留 30 份，最坏占用约 580 MB。建议：

- 只备份配置（默认）：每份约 45 KB，30 份不到 2 MB
- 需要数据库：把「保留份数」降到 10 左右

## 指令

| 指令 | 权限 | 说明 |
|---|---|---|
| `/备份配置` | 管理员 | 立即执行一次备份，回复文件名、文件数、大小、目录 |
| `/备份列表` | 管理员 | 查看最近 10 份备份的时间、大小、文件名 |

## 文件位置

```
$ASTRBOT_DATA/backups/config/
├── config-20261105-085128.zip   # 仅配置，约 45 KB
└── config-20261105-085850.zip   # 含数据库，约 20 MB
```

zip 内部保持相对路径，直接解压回 `data` 目录即可：

```
cmd_config.json
config/astrbot_plugin_bilibili_config.json
config/astrbot_plugin_config_backup_config.json
...
data_v4.db                     # 仅在开启数据库备份时存在
```

## 恢复方法（重要）

**不要**在容器运行中直接覆盖文件。

AstrBot 启动时把配置读进内存，停止的那一刻会把内存里的配置写回 `cmd_config.json`。你在容器运行时覆盖，文件会被它关停时的回写覆盖掉，看起来恢复了，重启后又变回原样。数据库文件更严重：运行中覆盖可能导致库损坏。

下面是最短的可靠流程。开始前先在一个 shell 里定义两个变量，后面所有命令都用它们：

```bash
ASTRBOT_DATA=/path/to/astrbot/data   # 换成你的数据目录
CONTAINER=astrbot                   # 换成你的容器名
```

### 1. 先查看有哪些备份

```bash
ls -la --time-style=full-iso "$ASTRBOT_DATA/backups/config/"
```

### 2. 停容器

```bash
docker stop "$CONTAINER"
```

确认容器真的停了：

```bash
docker ps -a --filter "name=$CONTAINER"
```

`STATUS` 应该是 `Exited (0)`。

### 3. 备份当前状态（保险用，强烈建议）

```bash
cd "$ASTRBOT_DATA"
cp -a cmd_config.json cmd_config.json.before-restore
cp -a config config.before-restore
```

### 4. 解压备份

```bash
cd "$ASTRBOT_DATA"
unzip -o backups/config/config-20261105-085850.zip
```

只想恢复某一个文件时，单独解压即可，例如只恢复核心配置：

```bash
cd "$ASTRBOT_DATA"
unzip -o backups/config/config-20261105-085850.zip cmd_config.json
```

### 5. 如果恢复了数据库，清理残留的 WAL 文件

SQLite 在 WAL 模式下运行时会在库旁边生成 `data_v4.db-wal` 和 `data_v4.db-shm`。用旧备份替换 `data_v4.db` 后，如果这两个残留文件还在，新的库会带着旧 WAL 一起启动，可能出现数据错乱。所以恢复数据库时要一并删掉：

```bash
cd "$ASTRBOT_DATA"
rm -f data_v4.db-wal data_v4.db-shm
```

### 6. 启动并验证

```bash
docker start "$CONTAINER"
sleep 30
docker logs "$CONTAINER" --since 1m 2>&1 | grep -E "AstrBot v|WebUI is ready|ERROR"
```

看到 `AstrBot vX.Y.Z` 和 `WebUI is ready` 就说明起来了。然后打开 WebUI，检查：

- 默认对话模型 / 回退模型是否是你预期的
- 人设是否恢复
- 插件的配置是否都在
- 最大步数、工具超时等数值

### 7. 如果恢复后不满意，退回原状态

```bash
docker stop "$CONTAINER"
cd "$ASTRBOT_DATA"
cp -a cmd_config.json.before-restore cmd_config.json
rm -rf config && cp -a config.before-restore config
docker start "$CONTAINER"
```

## 为什么恢复要停容器，而不是让插件自己恢复

插件运行在 AstrBot 进程内部，它没有能力停掉自己所在的容器。除非把宿主机的 Docker socket 挂进容器（等于给容器宿主机 root 权限），否则无法做到「点一下就自动恢复」。

所以本插件只做备份，不做恢复。恢复是宿主机层面的运维动作，按上面步骤执行最稳。

## 目录结构

```
astrbot_plugin_config_backup/
├── main.py           # 插件实现
├── metadata.yaml     # 插件元信息
├── _conf_schema.json # WebUI 配置页字段定义
├── README.md
└── LICENSE
```

## 已知限制

- 备份的是配置文件，不包含 `data/plugin_data/` 下各插件自己写的业务数据（除非你在「备份文件范围」里显式加上对应 glob）
- 数据库备份耗时会随库体积增长，50 MB 大约需要几秒
- 容器被重建不会丢备份（备份目录在 `data` 卷内），但**容器重建会回滚版本**，恢复配置并不能把代码版本一起还原。版本问题请先用源码或镜像 tag 修好，再恢复配置

## License

MIT
