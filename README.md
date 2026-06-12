139Pan Manager

https://img.shields.io/badge/license-MIT-blue.svg
https://img.shields.io/badge/python-3.11%2B-blue.svg
https://img.shields.io/badge/FastAPI-0.100%2B-green.svg
https://img.shields.io/badge/AList-3.61.0%2B-orange.svg

移动云盘分享链接挂载管理与 STRM 生成工具箱

139Pan Manager 是一个轻量级 Web 管理工具，专门用于将中国移动云盘（139 云盘）的分享链接挂载到 AList 中，并自动为视频文件生成 STRM 文件（Emby / Jellyfin / Plex 可直接读取的快捷方式）。它简化了手动添加大量分享链接、管理令牌、同步目录结构以及生成 STRM 映射的繁琐流程，提供友好的可视化界面和批量操作能力。

依赖要求：本项目依赖于 AList，推荐使用 AList v3.61.0 或更高版本。

---

✨ 功能特性

· 批量链接识别
    支持完整 URL、纯分享 ID、名称:分享ID 混合格式，自动抓取分享标题。
· 多令牌管理
    支持多个 139 云盘 Authorization 令牌，可随时切换、编辑、删除。
· 一键挂载到 AList
    根据分享 ID 和挂载路径，自动在 AList 中创建 139Yun 驱动类型的存储。
· STRM 自动生成
    递归扫描挂载目录下的视频文件，按配置的本地保存路径生成 .strm 文件（内容为 WebDAV 链接），支持增量更新与孤立文件清理。
· 目录定时刷新
    可为每个挂载点单独设置自动刷新间隔，保持 AList 文件列表与云盘同步。
· 批量操作
  · 批量生成 STRM
  · 批量修改 WebDAV 基准 URL（重写所有 .strm 文件内容）
  · 批量更换挂载路径前缀
  · 批量转移令牌
  · 批量启用/禁用存储
  · 批量删除（同时删除本地 STRM 文件和空文件夹）
· 空文件夹清理
    可配置删除无 .strm 文件的文件夹或仅删除完全空的文件夹。
· 实时任务日志
    生成 STRM 时显示进度和详细日志，支持手动终止任务。
· 深色模式 / 移动端适配
    响应式界面，底部导航栏，统计数据卡片紧凑展示。

---

🛠️ 技术栈

类别 技术 / 组件
后端语言 Python 3.11+
Web 框架 FastAPI + Uvicorn
数据库 SQLite3（内置）
任务调度 APScheduler
前端 Alpine.js + TailwindCSS（CDN）
图标库 Font Awesome 6
运行时 依赖 AList v3.61.0+

---

📦 安装与运行

方式一：Python 直接运行（适用于本地测试或服务器部署）

1. 克隆项目
   ```bash
   git clone https://github.com/your-username/139Pan-Manager.git
   cd 139Pan-Manager
   ```
2. 创建虚拟环境（推荐）
   ```bash
   python3.11 -m venv venv
   source venv/bin/activate      # Linux / macOS
   # 或 venv\Scripts\activate   (Windows)
   ```
3. 安装依赖
   ```bash
   pip install -r requirements.txt
   ```
   requirements.txt 内容：
   ```
   fastapi>=0.100.0
   uvicorn[standard]>=0.23.0
   requests>=2.31.0
   apscheduler>=3.10.0
   pydantic>=2.0.0
   ```
4. 准备 alist_client.py
      请将实现了 AlistClient 类的文件放置于项目根目录（需提供与 AList API 交互的封装）。
5. 启动服务
   ```bash
   python main.py
   ```
   默认监听 http://0.0.0.0:5240。打开浏览器访问即可。

方式二：在飞牛OS（FnOS）中安装（FPK 应用）

本项目已适配飞牛OS 应用规范，可直接打包为 .fpk 文件安装。

1. 安装飞牛OS 打包工具 fnpack（参考官方文档）。
2. 在项目根目录执行打包命令：
   ```bash
   fnpack build 139PanManager
   ```
3. 在飞牛OS 应用中心手动安装生成的 139PanManager.fpk。

飞牛OS 环境变量适配说明：

· 用户数据（数据库、日志）默认写入 ${TRIM_PKGHOME}（卸载时可选择保留或删除）。
· 运行时临时数据（PID、临时日志）存放于 ${TRIM_PKGVAR}。
· 卸载回调 cmd/uninstall_callback 会根据用户选择清理数据。

---

⚙️ 配置说明

首次使用需完成以下配置：

1. AList 连接设置
      在“设置中心”填写 AList 服务地址、账号和密码，并保存。
2. 添加云盘令牌
      在“云盘令牌管理”中添加你的 139 云盘 Authorization（获取方式：浏览器 F12 → Network → 请求头中的 Authorization 字段）。
3. 导入分享链接
      在“批量添加链接”中粘贴分享链接（每行一个），选择令牌，点击“开始提取”后确认导入。
4. 生成 STRM 文件
      在“网盘连接管理”中选中挂载点，点击“生成STRM”，等待任务完成。

---

📝 使用示例

批量链接输入框支持以下任意格式（每行一个）：

```
阿凡达 https://yun.139.com/w/i/xxxxxxxxxxxxx
2uETYymsjSxxx
我的电影:2uETYymsjSxxx
https://yun.139.com/shareweb/#/w/i/xxxxxxxxxxxxx
```

---

⚠️ 注意事项

· 本工具不存储任何云盘文件，仅管理 AList 中的存储配置和本地 .strm 映射文件。
· 生成的 .strm 文件内容为 WebDAV 链接，需要 Emby / Jellyfin / Plex 等媒体服务器能够访问该地址。
· 删除挂载点时，会同时删除对应的本地 .strm 文件及空文件夹（不会删除其他数据）。
· 建议定期备份数据目录：
  · 普通部署：项目目录下的 data/ 文件夹
  · 飞牛OS 环境：/vol1/@apphome/139PanManager/data.db

---

🤝 贡献指南

欢迎通过 Issue 和 Pull Request 参与贡献。在提交代码前请确保：

· 代码符合 PEP 8 规范
· 添加必要的注释和文档
· 测试通过

---

📄 开源许可

本项目采用 MIT License 许可协议。

---

🔗 相关链接

· AList 官方文档
· 飞牛OS 开发者平台
· 中国移动云盘官网