# 139Pan Manager

> 移动云盘分享链接挂载管理与 STRM 生成工具箱

[![AList](https://img.shields.io/badge/AList-3.61.0%2B-orange.svg)](https://github.com/AlistGo/alist)
[![GitHub](https://img.shields.io/badge/GitHub-139Pan--Manager-blue?logo=github)](https://github.com/liu6qiang123/139PanManager2strm)

## 📖 简介

139Pan Manager 是一个轻量级 Web 工具，用于将**中国移动云盘（139 云盘）分享链接**挂载到 [AList](https://github.com/AlistGo/alist) 中，并自动为视频文件生成 **STRM 文件**（飞牛影视 / Emby / Jellyfin / Plex 可直接读取的快捷方式）。
注：移动云盘分享链接的视频流分辨率为 1080p，画质足以满足日常观影需求 😂。


✨ 主要功能

· 批量识别分享链接 – 支持 URL、名称：链接、纯 ID、名称:ID 混合格式，

· 多令牌管理 – 添加、编辑、删除云盘授权令牌，支持批量转移alist移动盘令牌
· 自动创建 AList 存储 – 一键挂载分享链接到 AList，支持定时刷新目录
· STRM 文件生成 – 递归扫描视频，按配置路径生成 .strm 文件（WebDAV 链接）
· 增量更新 – 仅处理新增或变化的文件，自动清理孤立 STRM
· 空文件夹清理 – 可选激进/保守两种清理规则
· 批量操作 – 批量生成/删除/启用/禁用、批量修改 WebDAV 基准 URL 和挂载路径
· 实时任务日志 – 查看生成进度，支持手动终止
· 响应式界面 – 桌面端/移动端适配，深色模式

🔗 依赖

· Python 3.11+ / AList v3.61+ / FastAPI / SQLite

## 🚀 运行环境

- Python 3.11
- AList 服务（推荐 v3.61.0+）

## 📝 技术栈

- Python 3.11 + FastAPI + Uvicorn
- SQLite3（数据持久化）
- Alpine.js + TailwindCSS（前端）
- APScheduler（定时任务）
- 飞牛OS 打包（.fpk）

## 📦 安装与运行

```bash
# 克隆项目
git clone https://github.com/liu6qiang123/139PanManager2strm.git
cd 139PanManager2strm

# 安装依赖
pip install -r requirements.txt

# 启动服务
python3 main.py
