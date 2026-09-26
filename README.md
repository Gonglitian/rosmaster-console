# rosmaster-console：小车中控台

用浏览器控制 Yahboom ROSMASTER X3 麦轮小车（车载电脑是树莓派 5）。手机和电脑都不用装 ROS：在同一个局域网里打开 `http://rosmaster.local:8080` 就能看到雷达俯视图和相机画面，也能开车和急停。

设计的来龙去脉见决策记录：`Human-Following/2026-09-26_小车中控台_架构决策/架构决策记录.md`。

## 三个角色

- **hub（中控服务）**：跑在小车上的 Python 服务，是唯一直接接触 ROS 的程序。它负责起停传感器、仲裁控制权、执行安全规则，并提供网页和 WebSocket。
- **面板**：hub 自带的网页（`web/`），手机优先的布局。
- **worker（跑 policy 的进程）**：第二阶段再做。装在任意笔记本上的普通 Python 程序，通过 WebSocket 连上 hub，收传感器数据、跑跟随管线、发回速度指令。

## 目录

```
car/        小车端：Dockerfile、安装脚本、启动文件，以及只听 hub 的安全底盘驱动
hub/        中控服务（Python 3.8，唯一依赖 tornado）
web/        面板（纯 HTML/JS，不需要构建）
docs/       协议说明
scripts/    开发机上用的部署脚本
tests/      单元测试（不依赖 ROS）
```

## 安装和更新

开发在 tasl-l1 上进行（它和小车在同一个局域网里）：

```bash
bash scripts/deploy_car.sh --install   # 第一次：同步代码，在车上构建镜像并创建 rc-hub 容器
bash scripts/deploy_car.sh             # 以后：同步代码，重启 hub
```

`car/install_hub.sh` 在车上做四件事：

1. 把 Yahboom 原来的 `x3` 容器存成快照 `rc-base:x3-20260926`，作为基础镜像，保证 ROS 环境和论文实验时完全一致；
2. 在快照上装 tornado；
3. 停掉旧的 `x3` 容器（两个底盘驱动会争用串口）；
4. 创建开机自启的 `rc-hub` 容器。容器用特权模式运行，因为雷达启动失败时需要做 USB 软拔插。

## 安全模型

详见 `docs/protocol.md` 最后一节。要点：

- 小车只听 hub 的：底盘驱动换成了 `car/nodes/hf_driver_x3.py`，它只订阅 `/hub/cmd_vel`，不再订阅 `/cmd_vel`。所以局域网里别的机器发 `/cmd_vel` 会被忽略，包括 tasl-l1 上原来的 `start_real_robot.sh`。
- 急停 > 手动 > policy。手动一碰就接管，按按钮才交还。
- 当前的指令来源 0.5 s 没发指令就停车；hub 本身挂了，驱动自带的 0.5 s 看门狗也会停车。
- 限速 0.7 m/s、1.5 rad/s，加速度上限 1.5 m/s²，都在小车上强制执行。

## 回到旧流程

在车上执行 `docker stop rc-hub && docker start x3`，然后按 tasl-l1 `~/human-following/scripts/start_real_robot.sh` 的注释操作。不要让两个容器同时驱动底盘。

## 测试

```bash
python3 -m pytest tests/        # 或 python3 -m unittest discover tests
```
