# Hub 协议（v0.1，第一阶段）

hub 是跑在小车上的中控服务，也是全系统唯一直接接触 ROS 的程序。其他设备全部通过 HTTP 和 WebSocket 连它，不需要装 ROS。

| 地址 | 用途 |
|---|---|
| `http://rosmaster.local:8080/` | 网页面板 |
| `ws://rosmaster.local:8080/ws` | 面板协议（JSON 文本帧），见下 |
| `http://rosmaster.local:8080/camera.mjpg` | 相机画面：MJPEG（逐帧 JPEG 的 multipart 流），每个观看者最多 15 fps |
| `http://rosmaster.local:8080/api/state` | 当前状态的一次性 JSON 快照，内容与下面的 `state` 消息相同 |

## 时间

hub 发出的所有时间（`stamp`、`s`、`server_time`）都是**小车的系统时钟**，单位是 Unix 秒。树莓派 5 没有带电池的硬件时钟，联网后才靠 NTP 对时，所以不要拿小车的时间和别的机器的时间直接相减。要测延迟，用下面的往返测法：`ping`/`pong`，以及第二阶段 worker 协议里的观测回显。

## 客户端 → hub

| `t` | 字段 | 作用 |
|---|---|---|
| `manual` | `vx`, `vy`（m/s），`wz`（rad/s），`seq` | 手动速度。一收到就切到手动模式，policy 立即被屏蔽。操作期间面板每 100 ms 发一次；超过 0.5 s 没收到新指令，hub 就输出零速度 |
| `manual_release` | — | 松手：速度归零，但仍保持手动模式 |
| `hand_back` | — | 结束手动：有激活的 policy 就交还给它，没有就回到空闲 |
| `estop` | — | 急停。锁存生效，并立刻下发零速度 |
| `estop_release` | — | 解除急停，回到空闲。policy 不会自动恢复 |
| `sensor` | `name`：`base`（底盘）/ `lidar`（雷达）/ `camera`（相机），`on`：bool | 开关传感器 |
| `beep` | — | 鸣笛 0.15 s（底盘开着时才有效） |
| `ping` | `c`：客户端时间 | hub 回 `pong` |

## hub → 客户端

| `t` | 频率 | 内容 |
|---|---|---|
| `hello` | 连接时 | `client_id`，`config`（`laser_x`、`laser_yaw`、`max_linear`、`max_angular`、`control_hz`、`source_timeout`、`camera_max_fps`） |
| `state` | 5 Hz，状态变化时立即补发 | `control`（`mode`：idle/manual/policy，`estop`，`output`：当前下发的 [vx, vy, wz]，`source_age`，`limits`）、`sensors`（每个传感器的 `state`：off/starting/on/error/stopping，以及 `message`、`attempt`）、`topics`（scan/odom/voltage 的接收频率和最后一次收到距今的秒数）、`battery_v`、`clients`、`uptime`、`sys`（`ip`、`disk_free_gb`、`cpu_temp_c`） |
| `scan` | 每帧雷达（约 6.7 Hz） | `seq`、`stamp`（小车收到的时间）、`hstamp`（雷达驱动打的时间戳）、`amin`、`ainc`、`cm`：每个角度的距离（厘米，整数，0 表示无效）。数据在 `laser` 坐标系下，它和车体坐标系 `base_link` 的关系是：x 方向偏移 `laser_x`，绕竖轴转了 `laser_yaw` |
| `odom` | 约 10 Hz | EKF 融合后的 `/odom`：`x`、`y`、`yaw`、`vx`、`vy`、`wz` |
| `pong` | 回应 `ping` | `c`（原样返回）、`s`（小车时间） |
| `ack` | 回应 `manual` | `seq`、`s` |

## 控制权与安全规则

所有规则都在小车上执行：

1. 优先级：急停 > 手动 > policy。任何客户端都能急停；急停锁存，要显式解除。
2. hub 以 20 Hz 向 `/hub/cmd_vel` 发布仲裁后的速度，不管有没有人在开车都一直发。
3. 限速：|(vx, vy)| ≤ 0.7 m/s，|wz| ≤ 1.5 rad/s。加速度上限：线加速度 1.5 m/s²，角加速度 4 rad/s²。减速（往零的方向）不受限；速度方向反转时先归零。
4. 当前掌控的来源超过 0.5 s 没发指令，就按零处理。
5. 底盘驱动（`car/nodes/hf_driver_x3.py`）只听 `/hub/cmd_vel`，并且自带 0.5 s 看门狗。原厂驱动会一直保持最后一个速度。
6. 底盘驱动内部还有硬限幅：1.0 m/s、3.0 rad/s，作为最后一道保险。

### 出故障时车会不会停

| 故障 | 结果 | 靠什么停 |
|---|---|---|
| 面板断线或不再发指令 | 0.5 s 内停 | hub 把静默的来源当作零 |
| hub 卡死（进程还在，事件循环不动了） | 0.5 s 内停（待架空车轮验证） | 驱动看门狗：`/hub/cmd_vel` 断了 |
| hub 进程崩溃 | 0.5 s 内停，随后 hub 自动重启，传感器全部回到关闭 | 容器入口 `car/entrypoint.sh` 是一个看护脚本，不会随 hub 一起退出，所以驱动还活着，由它的看门狗停车；随后看护脚本清理残留进程，经串口再补发一次零速度（`car/stop_wheels.py`）。看护流程 2026-09-26 在车上实测过；但看门狗真的能让转动中的轮子停下，还要架空车轮再验证一次 |
| 整个容器被杀、驱动进程崩溃、树莓派断电 | **未验证** | 只能靠底盘主控板（STM32 单片机）自己。它在串口指令停止后会不会继续保持最后速度，还没测过。容器下次启动时会先补发一次零速度 |
