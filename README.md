# 139Pan Manager

> 移动云盘分享链接挂载管理与 STRM 生成工具箱

[![AList](https://img.shields.io/badge/AList-3.61.0%2B-orange.svg)](https://alist.nn.ci/)
[![GitHub](https://img.shields.io/badge/GitHub-139Pan--Manager-blue?logo=github)](https://github.com/yourname/139Pan-Manager)

## 📖 简介

139Pan Manager 是一个轻量级 Web 工具，用于将**中国移动云盘（139 云盘）分享链接**挂载到 [AList](https://alist.nn.ci/) 中，并自动为视频文件生成 **STRM 文件**（Emby / Jellyfin / Plex 可直接读取的快捷方式）。

- 批量导入分享链接（支持完整 URL、纯分享 ID、`名称:ID` 格式）
- 多令牌管理
- 自动创建 AList 存储
- 递归扫描生成 STRM 文件
- 定时刷新目录
- 批量修改 WebDAV 基准 URL
- 批量转移令牌
- 空文件夹清理

## 🚀 运行环境

- Python 3.11
- AList 服务（推荐 v3.61.0+）

## 📦 安装与运行

```bash
# 克隆项目
git clone https://github.com/yourname/139Pan-Manager.git
cd 139Pan-Manager

# 安装依赖
pip install -r requirements.txt

# 启动服务
python3 main.py
