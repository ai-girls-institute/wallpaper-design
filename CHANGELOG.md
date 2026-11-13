# 版本记录

## v0.1.4-beta.1 — 2026-11-13 (Beta)

- Fix stale HWiNFO temperature replay when the newest record is invalid or incomplete.
- Keep stop/restart working when runtime-state persistence is temporarily unavailable.
- Validate and save complete configuration requests before changing the active selection; preserve the prior selection on failure.
- Report saved login-startup state from the same file used by installation.
- Include login startup and tray operation from the previously local-only collector candidates for beta testing.
- Wallpaper composition, audio and parallax remain based on v0.1.3. This beta does not claim to resolve long-term CEF memory growth.
- Isolated source and EXE checks passed; real login/reboot and cross-device behavior still need user trials.

## v0.1.3 — 2026-10-14

- 新增轻量鼠标视差：场景整图和远近悬浮窗按不同幅度移动，沿用当前布局、透视、浮动及悬停。
- 新增视差开关、0–200%强度、100–800ms缓动、移开回正四项属性；默认100%/240ms/回正，共28项用户属性。
- 关闭视差或强度0恢复原构图；系统减少动态、暂停与恢复、不同适屏方式继续受控；悬停坐标包含视差位移。
- 发布用户选定的192步SVG声痕绘制、固定音频缓冲、数字节点复用和静音/重复样式更新优化，保留原音频聚合与幅度公式。
- 采集器沿用已发行的v0.1.2；登录后台候选与GPU滤镜实验仍保留为本地候选。

本机重载后的视差获用户确认，非视觉技术检查通过。长期内存增长尚未解决，不声明已修复泄漏；GitHub发布不自动更新创意工坊。


## v0.1.2 — 2026-10-11

- 新增独立Windows64采集器EXE与配置窗口，用户无需安装Python。
- 自动查找创意工坊下载目录，兼容`Web`/`web`类型，生成每次安装独立的连接配置；端口占用时避让。
- EXE内可启用Alienware温度辅助采集，需用户确认Windows管理员授权；已有新鲜采集可自动只读接入，CPU传感器归属由用户确认。
- 按用户选择，默认数据保存到壁纸下载目录的`telemetry-runtime`；保留原连接文件备份。
- 修复温度来源页隐藏控件和错误提示，独立保存HWiNFO/Alienware来源路径。
- 提供采集器单独ZIP、含EXE的完整包、源代码与构建说明；README与订阅安装教程同步。
- 壁纸场景、布局、音频图形及属性键沿用v0.1.1。当前仍为手动运行，不登记登录任务。

候选5的本机试用获用户确认。已完成非视觉技术检查；其他设备、首次UAC启动和Steam更新后的配置保留未做全面验证。

## v0.1.1 — 2026-10-08

- 接入 Wallpaper Engine 原生左右各64频段音频；引擎内音乐响应无需额外采集器。
- 15号主频道采用居中433px对称声痕及PEAK BIN / CENTROID分析，静音回到中央水平线，0–1响应映射为0–37px半高。
- 声道及三频段采用最强25%频段RMS与敏感映射；声痕结合整体/局部输入，保留音频开关、灵敏度及暂停/帧率属性。
- 可选硬件采集器新增普通权限、当前用户Windows任务，支持登录启动、每分钟补启动、子进程退出重试及端口占用等待。
- host和采集器日志各按2MiB轮换并保留上一份；取消HTTP读取不再打印连接中断堆栈。
- 沿用3440×1440场景、默认14窗布局、属性键及通用安装配置；README旧动图明确标注为v0.1.0演示。

新版音频、安装与运行效果待使用者人眼检验，未运行自动比较、检验、测试、审计或评分。

## v0.1.0 — 2026-10-05

- 3440×1440 星海观测室场景与默认 14 窗布局，07 / 08 固定隐藏。
- 原生 SVG 窗口、Oxanium / Rajdhani 字体、150% 默认浮动与悬停高亮。
- 24 项 Wallpaper Engine 原生属性，包含动效、窗口亮度与不透明度、音频、更新档位、适屏及逐窗显隐。
- 默认硬件请求 250ms、音频请求 33ms、动画目标 60fps；响应属性、总帧率设置和暂停 / 恢复。
- 可选 Windows / NVIDIA 硬件采集、HWiNFO CSV 和 Alienware 导出、WASAPI 系统播放音频响应。
- 通用安装配置与每次安装独立生成的读取密钥。
- 发行文件采用相对路径；不包含制作机器的配置、密钥、实时数据、日志、缓存或私用恢复包。

通用安装与本版运行效果待使用者人眼检验。
