# 独立采集器源码

普通用户直接下载Release中的Windows64 EXE包，不需要Python。

## 构建

本次构建使用Windows64 Python3.12。此目录包含主程序、HTTP服务、只读硬件逻辑及内置AWCC辅助脚本。

```powershell
python --version
python -m venv .build-env
./.build-env/Scripts/python.exe -m pip install -r build-requirements.txt
./build.ps1
```

输出为`dist/Mornye-Telemetry.exe`。构建脚本先规范PowerShell资源换行并重建辅助脚本/安装器完整性摘要，再打包配置页、托盘、登录启动及温度任务资源，兼容Git检出/源码ZIP的换行差异。主程序普通权限运行，管理员温度操作仅使用内置资源。

```powershell
./.build-env/Scripts/python.exe -m unittest discover -s tests -v
```

测试使用临时目录、端口和模拟管理员启动器，不触发UAC。部分原有测试读取当前机器的只读硬件指标；不改变硬件设置。托盘测试需要可用桌面会话。GUI及视觉验收由用户完成。

新增体检回归使用假硬件引擎、临时HTTP服务和Windows文件占用：

```powershell
./.build-env/Scripts/python.exe -B checks/telemetry_regressions.py --source . --temp-root build/test-temp
```

可加`--phase F1`至`F4`单独运行；旧Python采集器可使用`--source ../telemetry`，不适用的登录/独立进程测试会跳过。

主程序普通权限。点击温度启用时，通过系统PowerShell和Windows runas/UAC启动只读辅助；它用共享互斥、独立停止信号和心跳租约管理本次采集。数据默认位于选择的壁纸目录telemetry-runtime。

本Beta还包含可选登录自动采集及托盘运行。启用登录启动时，普通任务指向独立版本EXE；需要持久温度任务时，通过明确的UAC操作安装受保护辅助脚本。登录时不弹出管理员授权。实际重启登录和其他设备仍需试用。

## 旧Python采集器

仓库`telemetry/`和`setup-telemetry.ps1`保留v0.1.1的源码安装路径。其固定依赖的Windows完整预编译包路径为Python3.10；`telemetry/startup.py`登记的旧任务和独立EXE是不同运行方式。本版普通用户教程以EXE为入口，不自动迁移旧任务。
