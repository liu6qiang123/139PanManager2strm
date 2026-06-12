import os
import sys
import json
import sqlite3
import time
import shutil
import uuid
import re
import requests
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from typing import List, Optional, Dict
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

# ==================== 持久化数据目录配置（兼容飞牛OS与本地测试）====================
def get_data_root():
    """获取应用持久化数据的根目录。
    优先级：
    1. 飞牛OS 注入的 TRIM_PKGHOME（用户数据目录，卸载时可选择保留）
    2. 本地开发环境：当前文件所在目录下的 'data' 文件夹
    """
    home = os.getenv('TRIM_PKGHOME')
    if home:
        return home
    # 本地测试回退
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

DATA_ROOT = get_data_root()
os.makedirs(DATA_ROOT, exist_ok=True)

# 数据库文件路径
DB_PATH = os.path.join(DATA_ROOT, 'data.db')

# 日志目录
LOG_DIR = os.path.join(DATA_ROOT, 'log')
os.makedirs(LOG_DIR, exist_ok=True)

# 配置文件路径（备用，目前主要使用数据库）
CONFIG_PATH = os.path.join(DATA_ROOT, 'config.json')

# 旧版 JSON 配置文件位置（应用安装目录下的 data/db.json，用于迁移）
OLD_JSON_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'db.json')

# ==================== 日志配置 ====================
sys_logger = logging.getLogger("system")
sys_logger.setLevel(logging.INFO)
sys_handler = RotatingFileHandler(os.path.join(LOG_DIR, "system.log"), maxBytes=10*1024*1024, backupCount=5)
sys_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
sys_logger.addHandler(sys_handler)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
sys_logger.addHandler(console)

task_loggers = {}
def get_task_logger(task_id: str):
    if task_id not in task_loggers:
        logger = logging.getLogger(f"task_{task_id}")
        logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(os.path.join(LOG_DIR, f"task_{task_id}.log"), maxBytes=5*1024*1024, backupCount=2)
        handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        logger.addHandler(handler)
        task_loggers[task_id] = logger
    return task_loggers[task_id]

# ==================== 数据库初始化与迁移 ====================
def init_database():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode=WAL;")
    c = conn.cursor()
    
    # config 表存储 key-value 配置
    c.execute('''CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )''')
    
    # tokens 表
    c.execute('''CREATE TABLE IF NOT EXISTS tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        authorization TEXT NOT NULL,
        created_at REAL,
        updated_at REAL
    )''')
    
    # mount_points 表
    c.execute('''CREATE TABLE IF NOT EXISTS mount_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        storage_id INTEGER UNIQUE NOT NULL,
        share_link_id TEXT NOT NULL,
        mount_path TEXT NOT NULL,
        strm_subdir TEXT DEFAULT '',
        strm_save_root TEXT NOT NULL,
        remark TEXT,
        auto_refresh INTEGER DEFAULT 0,
        refresh_interval INTEGER DEFAULT 0,
        last_refresh_time REAL DEFAULT 0,
        last_generate_time REAL DEFAULT 0,
        created_at REAL,
        updated_at REAL
    )''')
    
    # strm_files 表，外键级联删除
    c.execute('''CREATE TABLE IF NOT EXISTS strm_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mount_point_id INTEGER NOT NULL,
        webdav_url TEXT,
        local_path TEXT UNIQUE,
        source_modified REAL,
        source_size INTEGER,
        created_at REAL,
        updated_at REAL,
        FOREIGN KEY(mount_point_id) REFERENCES mount_points(id) ON DELETE CASCADE
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_mount_point_id ON strm_files(mount_point_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_local_path ON strm_files(local_path)')
    
    # storage_defaults 表
    c.execute('''CREATE TABLE IF NOT EXISTS storage_defaults (
        id INTEGER PRIMARY KEY CHECK (id=1),
        driver TEXT,
        proxy_range INTEGER,
        webdav_policy TEXT,
        down_proxy_sign INTEGER
    )''')
    
    # 默认配置
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?,?)", ("default_root", json.dumps("/移动云盘分享")))
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?,?)", ("strm_config", json.dumps({"base_url": "", "save_root": "/strm"})))
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?,?)", ("clean_empty_folders", json.dumps(False)))
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?,?)", ("clean_rule", json.dumps(1)))
    c.execute("INSERT OR IGNORE INTO storage_defaults (id, driver, proxy_range, webdav_policy, down_proxy_sign) VALUES (1,'139Yun',1,'302_redirect',0)")
    
    conn.commit()
    
    # 迁移旧 JSON 数据（如果存在且数据库为空）
    if os.path.exists(OLD_JSON_CONFIG) and not os.path.exists(DB_PATH):
        try:
            with open(OLD_JSON_CONFIG, "r", encoding="utf-8") as f:
                old_data = json.load(f)
            # 导入 tokens
            for token in old_data.get("tokens", []):
                c.execute("INSERT OR IGNORE INTO tokens (id, name, authorization, created_at, updated_at) VALUES (?,?,?,?,?)",
                          (token.get("id"), token["name"], token["authorization"], time.time(), time.time()))
            # 导入 alist_config
            if old_data.get("alist_config"):
                c.execute("REPLACE INTO config (key, value) VALUES (?,?)", ("alist_config", json.dumps(old_data["alist_config"])))
            # 导入 strm_config
            strm_cfg = old_data.get("strm_config", {"base_url": "", "save_root": "/strm"})
            c.execute("REPLACE INTO config (key, value) VALUES (?,?)", ("strm_config", json.dumps(strm_cfg)))
            # 导入 default_root
            c.execute("REPLACE INTO config (key, value) VALUES (?,?)", ("default_root", json.dumps(old_data.get("default_root", "/移动云盘分享"))))
            # 导入 storage_defaults
            defaults = old_data.get("storage_defaults", {"driver": "139Yun", "proxy_range": True})
            c.execute("REPLACE INTO storage_defaults (id, driver, proxy_range, webdav_policy, down_proxy_sign) VALUES (1,?,?,?,?)",
                      (defaults.get("driver"), 1 if defaults.get("proxy_range") else 0, defaults.get("webdav_policy", "302_redirect"), 1 if defaults.get("down_proxy_sign") else 0))
            conn.commit()
            shutil.move(OLD_JSON_CONFIG, OLD_JSON_CONFIG + ".bak")
            sys_logger.info("已从旧 db.json 迁移数据到数据库")
        except Exception as e:
            sys_logger.error(f"迁移失败: {e}")
    
    conn.close()
    sys_logger.info(f"数据库初始化完成: {DB_PATH}")

init_database()

# ==================== 数据库辅助函数 ====================
def get_config(key: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return json.loads(row[0]) if row else None

def set_config(key: str, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("REPLACE INTO config (key, value) VALUES (?,?)", (key, json.dumps(value)))
    conn.commit()
    conn.close()

def get_tokens():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, authorization FROM tokens ORDER BY id")
    tokens = []
    for row in c.fetchall():
        auth = row[2]
        tokens.append({
            "id": row[0],
            "name": row[1],
            "authorization": auth,
            "authorization_masked": auth[:12] + "********" if len(auth) > 20 else auth
        })
    conn.close()
    return tokens

def add_token(name: str, auth: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO tokens (name, authorization, created_at, updated_at) VALUES (?,?,?,?)", (name, auth, time.time(), time.time()))
    conn.commit()
    new_id = c.lastrowid
    conn.close()
    return new_id

def update_token(token_id: int, name: str, auth: str = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if auth:
        c.execute("UPDATE tokens SET name=?, authorization=?, updated_at=? WHERE id=?", (name, auth, time.time(), token_id))
    else:
        c.execute("UPDATE tokens SET name=?, updated_at=? WHERE id=?", (name, time.time(), token_id))
    conn.commit()
    conn.close()

def delete_token(token_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM tokens WHERE id=?", (token_id,))
    conn.commit()
    conn.close()

def get_storage_defaults():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT driver, proxy_range, webdav_policy, down_proxy_sign FROM storage_defaults WHERE id=1")
    row = c.fetchone()
    conn.close()
    if row:
        return {"driver": row[0], "proxy_range": bool(row[1]), "webdav_policy": row[2], "down_proxy_sign": bool(row[3])}
    return {"driver": "139Yun", "proxy_range": True, "webdav_policy": "302_redirect", "down_proxy_sign": False}

def set_storage_defaults(driver, proxy_range, webdav_policy, down_proxy_sign):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("REPLACE INTO storage_defaults (id, driver, proxy_range, webdav_policy, down_proxy_sign) VALUES (1,?,?,?,?)",
              (driver, 1 if proxy_range else 0, webdav_policy, 1 if down_proxy_sign else 0))
    conn.commit()
    conn.close()

def sync_mount_point(storage_id: int, share_link_id: str, mount_path: str,
                     strm_subdir: str, strm_save_root: str, remark: str = "",
                     auto_refresh: int = 0, refresh_interval: int = 0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO mount_points (storage_id, share_link_id, mount_path, strm_subdir, strm_save_root, remark, auto_refresh, refresh_interval, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(storage_id) DO UPDATE SET
            share_link_id = excluded.share_link_id,
            mount_path = excluded.mount_path,
            strm_subdir = excluded.strm_subdir,
            strm_save_root = excluded.strm_save_root,
            remark = excluded.remark,
            auto_refresh = excluded.auto_refresh,
            refresh_interval = excluded.refresh_interval,
            updated_at = excluded.updated_at
    ''', (storage_id, share_link_id, mount_path, strm_subdir, strm_save_root, remark, auto_refresh, refresh_interval, time.time(), time.time()))
    conn.commit()
    conn.close()

# ==================== AList 客户端 ====================
def get_alist_client():
    config = get_config("alist_config")
    if not config:
        raise HTTPException(status_code=400, detail="请先配置AList连接")
    try:
        from alist_client import AlistClient
        return AlistClient(config["url"], config["username"], config["password"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"AList连接失败: {str(e)}")

# ==================== 链接提取与标题获取 ====================
def extract_link_id(share_url: str) -> Optional[str]:
    raw_url = share_url.strip()
    pure_id_match = re.match(r'^[a-zA-Z0-9]{10,25}$', raw_url)
    if pure_id_match:
        return pure_id_match.group(0)
    if '#' in raw_url:
        base_url = raw_url.split('#')[0]
    else:
        base_url = raw_url
    patterns = [
        r'/w/i/([a-zA-Z0-9]+)',
        r'/g/i/([a-zA-Z0-9]+)',
        r'/m/i/([a-zA-Z0-9]+)',
        r'/m/i\?([a-zA-Z0-9]+)',
        r'/group/i\?([a-zA-Z0-9]+)',
        r'/shareweb/#/w/i/([a-zA-Z0-9]+)',
        r'/sharewap/#/m/i\?([a-zA-Z0-9]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_url)
        if match:
            return match.group(1)
    return None

def fetch_share_title(link_id: str) -> str:
    try:
        url = f"https://yun.139.com/shareweb/#/w/i/{link_id}"
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        match = re.search(r'<title>(.*?)</title>', resp.text, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            title = re.sub(r'\s*[-|]\s*(中国移动云盘|139云盘|139网盘).*$', '', title)
            if title and len(title) < 100:
                return title
    except:
        pass
    return link_id

# ==================== STRM 生成核心函数 ====================
video_exts = ['.mp4', '.mkv', '.m3u8', '.ts', '.avi', '.mov', '.flv', '.wmv', '.rmvb']

def generate_strm_for_path(client, strm_base_url: str, save_root: str, strm_subdir: str, mount_path: str,
                           alist_rel_path: str = "", task_id: str = None,
                           conn: sqlite3.Connection = None,
                           progress_counter: dict = None,
                           mount_point_id: int = None) -> int:
    if task_id and task_stop_flags.get(task_id, False):
        add_log(task_id, "[STOP] 任务已被用户终止")
        return 0
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA foreign_keys=ON")
        close_conn = True
    current_alist_path = mount_path.rstrip('/') + '/' + alist_rel_path.lstrip('/') if alist_rel_path else mount_path
    path_components = [save_root]
    if strm_subdir:
        path_components.append(strm_subdir)
    if alist_rel_path:
        path_components.append(alist_rel_path)
    current_save_dir = os.path.join(*path_components)
    os.makedirs(current_save_dir, exist_ok=True)
    remote_items = {}
    try:
        resp = client._request("POST", "/api/fs/list", data={"path": current_alist_path, "password": ""})
        if resp and isinstance(resp, dict) and "content" in resp:
            for item in resp["content"]:
                remote_items[item["name"]] = item
        else:
            add_log(task_id, f"[WARN] 无法获取远程列表 {current_alist_path}")
    except Exception as e:
        add_log(task_id, f"[ERROR] 获取远程列表失败 {current_alist_path}: {str(e)}")
        if close_conn:
            conn.close()
        return 0
    generated = 0
    c = conn.cursor()
    for name, item in remote_items.items():
        if task_id and task_stop_flags.get(task_id, False):
            break
        if item.get("is_dir"):
            sub_alist_rel = os.path.join(alist_rel_path, name) if alist_rel_path else name
            generated += generate_strm_for_path(client, strm_base_url, save_root, strm_subdir, mount_path, sub_alist_rel, task_id, conn, progress_counter, mount_point_id)
        else:
            ext = os.path.splitext(name)[1].lower()
            if ext in video_exts:
                if progress_counter is not None:
                    progress_counter["discovered"] = progress_counter.get("discovered", 0) + 1
                    if task_id in tasks_status:
                        tasks_status[task_id]["discovered"] = progress_counter["discovered"]
                        tasks_status[task_id]["processed"] = progress_counter.get("processed", 0)
                        tasks_status[task_id]["message"] = f"已发现 {progress_counter['discovered']} 个视频，已生成 {progress_counter.get('processed',0)} 个"
                        tasks_status[task_id]["log"] = log_buffer[task_id][-50:]
                strm_filename = os.path.splitext(name)[0] + ".strm"
                strm_filepath = os.path.join(current_save_dir, strm_filename)
                webdav_url = strm_base_url.rstrip('/') + current_alist_path + '/' + name
                c.execute("SELECT source_modified, webdav_url FROM strm_files WHERE local_path=?", (strm_filepath,))
                row = c.fetchone()
                need_gen = True
                if row:
                    old_modified, old_webdav = row
                    if old_modified == item.get("modified") and os.path.exists(strm_filepath):
                        need_gen = False
                        if old_webdav != webdav_url:
                            add_log(task_id, f"[UPDATE] 更新 WebDAV 链接: {name}")
                            with open(strm_filepath, 'w', encoding='utf-8') as f:
                                f.write(webdav_url)
                            c.execute("UPDATE strm_files SET webdav_url=?, updated_at=? WHERE local_path=?",
                                      (webdav_url, time.time(), strm_filepath))
                            if progress_counter is not None:
                                progress_counter["processed"] = progress_counter.get("processed", 0) + 1
                        continue
                if need_gen:
                    src_modified = None
                    modified_str = item.get("modified")
                    if modified_str:
                        try:
                            src_modified = datetime.fromisoformat(modified_str.replace('Z', '+00:00')).timestamp()
                        except:
                            src_modified = time.time()
                    try:
                        with open(strm_filepath, 'w', encoding='utf-8') as f:
                            f.write(webdav_url)
                        if src_modified:
                            os.utime(strm_filepath, (src_modified, src_modified))
                        c.execute('''INSERT OR REPLACE INTO strm_files 
                                     (local_path, webdav_url, mount_point_id, source_modified, source_size, created_at, updated_at)
                                     VALUES (?,?,?,?,?,?,?)''',
                                  (strm_filepath, webdav_url, mount_point_id, src_modified, item.get("size", 0), time.time(), time.time()))
                        generated += 1
                        add_log(task_id, f"[GENERATE] 生成: {name}")
                        if progress_counter is not None:
                            progress_counter["processed"] = progress_counter.get("processed", 0) + 1
                    except Exception as e:
                        add_log(task_id, f"[ERROR] 写入失败 {name}: {str(e)}")
    # 清理孤立 STRM 文件
    c.execute("SELECT local_path, webdav_url FROM strm_files WHERE mount_point_id=? AND local_path LIKE ?",
              (mount_point_id, current_save_dir + '/%'))
    all_matches = c.fetchall()
    for local_path, webdav_url in all_matches:
        if os.path.dirname(local_path) != current_save_dir:
            continue
        original_video_name = os.path.basename(webdav_url)
        if original_video_name not in remote_items:
            if os.path.exists(local_path):
                os.remove(local_path)
                add_log(task_id, f"[CLEANUP] 删除孤立 STRM: {local_path}")
            c.execute("DELETE FROM strm_files WHERE local_path=?", (local_path,))
    if close_conn:
        conn.commit()
        conn.close()
    return generated

# ==================== 定时刷新管理 ====================
refresh_jobs = {}
scheduler = BackgroundScheduler()
scheduler.start()

def add_refresh_job(storage_id: int, interval_minutes: int, mount_path: str):
    if interval_minutes <= 0:
        remove_refresh_job(storage_id)
        return
    job_id = f"refresh_{storage_id}"
    if job_id in refresh_jobs:
        try:
            scheduler.remove_job(job_id)
        except:
            pass
    try:
        trigger = IntervalTrigger(minutes=interval_minutes)
        job = scheduler.add_job(
            func=lambda: refresh_storage_content(storage_id, mount_path),
            trigger=trigger,
            id=job_id,
            replace_existing=True
        )
        refresh_jobs[job_id] = storage_id
        sys_logger.info(f"添加定时刷新任务: {mount_path} -> 每{interval_minutes}分钟")
    except Exception as e:
        sys_logger.error(f"添加定时任务失败 {mount_path}: {e}")

def remove_refresh_job(storage_id: int):
    job_id = f"refresh_{storage_id}"
    if job_id in refresh_jobs:
        try:
            scheduler.remove_job(job_id)
            del refresh_jobs[job_id]
            sys_logger.info(f"移除定时刷新任务: storage_id {storage_id}")
        except:
            pass

def refresh_storage_content(storage_id: int, mount_path: str):
    try:
        client = get_alist_client()
        client._request("POST", "/api/fs/list", data={"path": mount_path, "password": ""})
        sys_logger.info(f"定时刷新完成: {mount_path}")
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE mount_points SET last_refresh_time=? WHERE storage_id=?", (time.time(), storage_id))
        conn.commit()
        conn.close()
    except Exception as e:
        sys_logger.error(f"定时刷新失败 {mount_path}: {e}")

def load_refresh_jobs():
    try:
        client = get_alist_client()
        all_storages = client.list_storages()
        for s in all_storages:
            addition = json.loads(s.get("addition", "{}"))
            if addition.get("type") == "share" and addition.get("auto_refresh") and addition.get("refresh_interval", 0) > 0:
                add_refresh_job(s["id"], addition["refresh_interval"], s["mount_path"])
    except Exception as e:
        sys_logger.error(f"加载定时任务失败: {e}")

# ==================== 全局任务状态 ====================
tasks_status: Dict[str, Dict] = {}
log_buffer: Dict[str, List[str]] = {}
task_stop_flags: Dict[str, bool] = {}

def add_log(task_id: str, msg: str):
    if task_id not in log_buffer:
        log_buffer[task_id] = []
    log_buffer[task_id].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    if len(log_buffer[task_id]) > 200:
        log_buffer[task_id] = log_buffer[task_id][-200:]
    logger = get_task_logger(task_id)
    logger.info(msg)
    sys_logger.info(f"[任务{task_id}] {msg}")

# ==================== Pydantic 模型 ====================
class Token(BaseModel):
    id: Optional[int] = None
    name: str
    authorization: str

class AlistConfig(BaseModel):
    url: str
    username: str
    password: str

class DefaultRoot(BaseModel):
    path: str

class StrmConfig(BaseModel):
    base_url: str
    save_root: str
    clean_empty_folders: Optional[bool] = False
    clean_rule: Optional[int] = 1

class BatchItem(BaseModel):
    name: str
    link_id: str
    mount_path: str
    auto_refresh: bool = False
    refresh_interval: int = 0
    strm_subdir: str = ""

class BatchConfirmRequest(BaseModel):
    token_id: int
    items: List[BatchItem]

class BatchPrefixRequest(BaseModel):
    storage_ids: List[int]
    new_prefix: str

class StrmGenerateRequest(BaseModel):
    storage_ids: List[int]

class RefreshConfigRequest(BaseModel):
    auto_refresh: bool
    refresh_interval: int

class BatchWebdavRequest(BaseModel):
    storage_ids: List[int]
    new_base_url: str

class StrmPathUpdateRequest(BaseModel):
    strm_subdir: str

class BatchChangeTokenRequest(BaseModel):
    storage_ids: List[int]
    token_id: int

# ==================== FastAPI 应用 ====================
app = FastAPI(title="移动云盘分享挂载管理器")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ==================== API 路由 ====================
@app.get("/api/alist/config")
def get_alist_config():
    cfg = get_config("alist_config")
    if cfg:
        return {"url": cfg["url"], "username": cfg["username"], "has_password": True}
    return None

@app.post("/api/alist/config")
def set_alist_config(config: AlistConfig):
    set_config("alist_config", config.dict())
    return {"success": True}

@app.get("/api/default_root")
def get_default_root():
    root = get_config("default_root")
    return {"path": root if root else "/移动云盘分享"}

@app.post("/api/default_root")
def set_default_root(root: DefaultRoot):
    set_config("default_root", root.path.rstrip('/'))
    return {"success": True}

@app.get("/api/strm_config")
def get_strm_config():
    cfg = get_config("strm_config") or {"base_url": "", "save_root": "/strm"}
    cfg["clean_empty_folders"] = get_config("clean_empty_folders") or False
    cfg["clean_rule"] = get_config("clean_rule") or 1
    return cfg

@app.post("/api/strm_config")
def set_strm_config(config: StrmConfig):
    set_config("strm_config", {"base_url": config.base_url, "save_root": config.save_root})
    set_config("clean_empty_folders", config.clean_empty_folders)
    set_config("clean_rule", config.clean_rule)
    return {"success": True}

@app.get("/api/tokens")
def list_tokens():
    return get_tokens()

@app.post("/api/tokens")
def create_token(token: Token):
    auth = token.authorization.strip()
    token_id = add_token(token.name, auth)
    return {"id": token_id, "name": token.name, "authorization": auth}

@app.put("/api/tokens/{token_id}")
def update_token(token_id: int, token: Token):
    auth = token.authorization.strip() if token.authorization else None
    update_token(token_id, token.name, auth)
    return {"success": True}

@app.delete("/api/tokens/{token_id}")
def delete_token_api(token_id: int):
    delete_token(token_id)
    return {"success": True}

@app.post("/api/parse-links")
async def parse_links(request: Request):
    raw_text = await request.body()
    raw_text = raw_text.decode('utf-8')
    lines = raw_text.strip().split('\n')
    items = []
    total_lines = 0
    success_count = 0
    failed_count = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        total_lines += 1
        if ':' in line and not line.startswith('http'):
            parts = line.split(':', 1)
            name = parts[0].strip()
            potential_id = parts[1].strip()
            link_id = extract_link_id(potential_id)
            if link_id:
                success_count += 1
                items.append({
                    "name": name[:50],
                    "link_id": link_id,
                    "url": potential_id,
                    "auto_refresh": False,
                    "refresh_interval": 0,
                    "strm_subdir": ""
                })
                continue
        if re.match(r'^[a-zA-Z0-9]{10,25}$', line):
            link_id = line
            name = ""
        else:
            url_match = re.search(r'https?://[^\s]+', line)
            if url_match:
                url = url_match.group(0)
                name_part = line[:url_match.start()].strip()
                name = name_part if name_part else ""
                link_id = extract_link_id(url)
            else:
                failed_count += 1
                continue
        if not link_id:
            failed_count += 1
            continue
        success_count += 1
        if not name:
            name = fetch_share_title(link_id)
        name = re.sub(r'[<>:"/\\|?*]', '', name)[:50]
        if not name:
            name = link_id
        items.append({
            "name": name,
            "link_id": link_id,
            "url": f"https://yun.139.com/w/i/{link_id}",
            "auto_refresh": False,
            "refresh_interval": 0,
            "strm_subdir": ""
        })
    return {
        "items": items,
        "total": total_lines,
        "success": success_count,
        "failed": failed_count
    }

@app.post("/api/storages/batch-confirm")
def batch_confirm(request: BatchConfirmRequest):
    client = get_alist_client()
    tokens = get_tokens()
    token = next((t for t in tokens if t["id"] == request.token_id), None)
    if not token:
        raise HTTPException(status_code=404, detail="选择的令牌不存在")
    strm_cfg = get_config("strm_config") or {"save_root": "/strm"}
    default_save_root = strm_cfg.get("save_root", "/strm")
    defaults = get_storage_defaults()
    results = []
    for item in request.items:
        link_id = item.link_id
        mount_path = item.mount_path.rstrip('/')
        name = item.name
        strm_subdir = item.strm_subdir.strip('/') if item.strm_subdir else ""
        addition = {
            "type": "share",
            "link_id": link_id,
            "authorization": token["authorization"].strip(),
            "root_folder_id": "root",
            "custom_upload_part_size": 0,
            "report_real_size": False,
            "use_large_thumbnail": True,
            "auto_refresh": item.auto_refresh,
            "refresh_interval": item.refresh_interval,
        }
        storage = {
            "mount_path": mount_path,
            "driver": defaults.get("driver", "139Yun"),
            "order": defaults.get("order", 0),
            "remark": name,
            "addition": json.dumps(addition),
            "enabled": defaults.get("enabled", True),
            "proxy_range": defaults.get("proxy_range", True),
            "webdav_policy": defaults.get("webdav_policy", "302_redirect"),
            "down_proxy_sign": defaults.get("down_proxy_sign", False),
            "cache_expiration": defaults.get("cache_expiration", 30),
            "web_proxy": defaults.get("web_proxy", False),
            "disable_index": defaults.get("disable_index", False),
            "enable_sign": defaults.get("enable_sign", False),
        }
        try:
            created = client.create_storage(storage)
            storage_id = created.get("id")
            sync_mount_point(storage_id, link_id, mount_path, strm_subdir, default_save_root, name,
                             1 if item.auto_refresh else 0, item.refresh_interval)
            if item.auto_refresh and item.refresh_interval > 0:
                add_refresh_job(storage_id, item.refresh_interval, mount_path)
            results.append({"link_id": link_id, "success": True, "mount_path": mount_path, "storage_id": storage_id})
        except Exception as e:
            results.append({"link_id": link_id, "success": False, "error": str(e)})
    return {"results": results}

@app.get("/api/storages")
def list_storages():
    client = get_alist_client()
    tokens = get_tokens()
    all_storages = client.list_storages()
    share_storages = []
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for s in all_storages:
        addition = json.loads(s.get("addition", "{}"))
        if addition.get("type") == "share":
            token_name = "未知"
            auth_target = addition.get("authorization", "").strip()
            for t in tokens:
                if t["authorization"].strip() == auth_target:
                    token_name = t["name"]
                    break
            c.execute('''SELECT strm_subdir, strm_save_root, auto_refresh, refresh_interval, last_refresh_time, last_generate_time 
                         FROM mount_points WHERE storage_id=?''', (s["id"],))
            row = c.fetchone()
            if row:
                strm_subdir, save_root, auto_refresh, refresh_interval, last_refresh, last_gen = row
                is_mapped = True
            else:
                strm_subdir, save_root, auto_refresh, refresh_interval, last_refresh, last_gen = "", "", 0, 0, 0, 0
                is_mapped = False
            share_storages.append({
                "id": s["id"],
                "mount_path": s.get("mount_path", ""),
                "remark": s.get("remark", ""),
                "link_id": addition.get("link_id", ""),
                "token_name": token_name,
                "enabled": not s.get("disabled", False),
                "auto_refresh": bool(auto_refresh) if is_mapped else addition.get("auto_refresh", False),
                "refresh_interval": refresh_interval if is_mapped else addition.get("refresh_interval", 0),
                "strm_subdir": strm_subdir,
                "strm_save_root": save_root,
                "is_mapped": is_mapped,
                "last_refresh_time": last_refresh,
                "last_generate_time": last_gen
            })
    conn.close()
    return share_storages

@app.post("/api/storage/{storage_id}/sync-mapping")
def sync_single_mapping(storage_id: int):
    client = get_alist_client()
    strm_cfg = get_config("strm_config") or {"save_root": "/strm"}
    default_save_root = strm_cfg.get("save_root", "/strm")
    all_storages = client.list_storages()
    target = next((s for s in all_storages if s["id"] == storage_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="存储不存在")
    addition = json.loads(target.get("addition", "{}"))
    link_id = addition.get("link_id", "")
    mount_path = target.get("mount_path", "")
    remark = target.get("remark", "")
    auto_refresh = addition.get("auto_refresh", False)
    refresh_interval = addition.get("refresh_interval", 0)
    strm_subdir = mount_path.rstrip('/').split('/')[-1] if mount_path else ""
    sync_mount_point(storage_id, link_id, mount_path, strm_subdir, default_save_root, remark,
                     1 if auto_refresh else 0, refresh_interval)
    return {"success": True}

@app.post("/api/storages/sync-all-mappings")
def sync_all_mappings():
    client = get_alist_client()
    strm_cfg = get_config("strm_config") or {"save_root": "/strm"}
    default_save_root = strm_cfg.get("save_root", "/strm")
    all_storages = client.list_storages()
    synced_count = 0
    for s in all_storages:
        addition = json.loads(s.get("addition", "{}"))
        if addition.get("type") == "share":
            storage_id = s["id"]
            link_id = addition.get("link_id", "")
            mount_path = s.get("mount_path", "")
            remark = s.get("remark", "")
            auto_refresh = addition.get("auto_refresh", False)
            refresh_interval = addition.get("refresh_interval", 0)
            strm_subdir = mount_path.rstrip('/').split('/')[-1] if mount_path else ""
            sync_mount_point(storage_id, link_id, mount_path, strm_subdir, default_save_root, remark,
                             1 if auto_refresh else 0, refresh_interval)
            synced_count += 1
    return {"success": True, "synced": synced_count}

@app.get("/api/storages/strm-stats")
def get_strm_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    client = get_alist_client()
    all_storages = client.list_storages()
    stats = []
    for s in all_storages:
        if s.get("driver") not in ["139Yun", "ChinaMobile", "中国移动云盘"]:
            continue
        storage_id = s["id"]
        c.execute('SELECT id FROM mount_points WHERE storage_id=?', (storage_id,))
        mp_row = c.fetchone()
        if mp_row:
            mp_id = mp_row[0]
            c.execute('SELECT COUNT(*) FROM strm_files WHERE mount_point_id=?', (mp_id,))
            count = c.fetchone()[0]
            c.execute('SELECT MAX(updated_at) FROM strm_files WHERE mount_point_id=?', (mp_id,))
            last = c.fetchone()[0] or 0
            stats.append({"storage_id": storage_id, "strm_count": count, "last_generate": last})
    conn.close()
    return stats

@app.post("/api/storages/batch-change-token")
def batch_change_token(request: BatchChangeTokenRequest):
    client = get_alist_client()
    tokens = get_tokens()
    token = next((t for t in tokens if t["id"] == request.token_id), None)
    if not token:
        raise HTTPException(status_code=404, detail="令牌不存在")
    new_auth = token["authorization"].strip()
    all_storages = {s["id"]: s for s in client.list_storages()}
    results = []
    for sid in request.storage_ids:
        if sid not in all_storages:
            results.append({"id": sid, "success": False, "error": "AList不存在该存储"})
            continue
        target = all_storages[sid]
        try:
            addition = json.loads(target.get("addition", "{}"))
            addition["authorization"] = new_auth
            target["addition"] = json.dumps(addition)
            client.update_storage(target)
            results.append({"id": sid, "success": True})
        except Exception as e:
            results.append({"id": sid, "success": False, "error": str(e)})
    return {"results": results}

@app.put("/api/storages/batch")
def batch_update_storages(request: dict):
    storage_ids = request.get("storage_ids", [])
    updates = request.get("updates", {})
    client = get_alist_client()
    all_storages = {s["id"]: s for s in client.list_storages()}
    results = []
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for sid in storage_ids:
        if sid not in all_storages:
            results.append({"id": sid, "success": False, "error": "存储不存在"})
            continue
        target = all_storages[sid]
        addition = json.loads(target.get("addition", "{}"))
        addition_changed = False
        for key, value in updates.items():
            if key == "remark":
                target["remark"] = value
                c.execute("UPDATE mount_points SET remark=?, updated_at=? WHERE storage_id=?", (value, time.time(), sid))
            elif key == "enabled":
                target["disabled"] = not value
            elif key == "mount_path":
                target["mount_path"] = value
                c.execute("UPDATE mount_points SET mount_path=?, updated_at=? WHERE storage_id=?", (value, time.time(), sid))
            elif key == "strm_subdir":
                c.execute("UPDATE mount_points SET strm_subdir=?, updated_at=? WHERE storage_id=?", (str(value).strip(), time.time(), sid))
            elif key == "auto_refresh":
                val_int = 1 if bool(value) else 0
                addition["auto_refresh"] = bool(value)
                addition_changed = True
                c.execute("UPDATE mount_points SET auto_refresh=?, updated_at=? WHERE storage_id=?", (val_int, time.time(), sid))
            elif key == "refresh_interval":
                addition["refresh_interval"] = int(value)
                addition_changed = True
                c.execute("UPDATE mount_points SET refresh_interval=?, updated_at=? WHERE storage_id=?", (int(value), time.time(), sid))
        if addition_changed:
            target["addition"] = json.dumps(addition)
        try:
            client.update_storage(target)
            if "auto_refresh" in updates or "refresh_interval" in updates:
                if addition.get("auto_refresh") and addition.get("refresh_interval", 0) > 0:
                    add_refresh_job(target["id"], addition["refresh_interval"], target.get("mount_path", ""))
                else:
                    remove_refresh_job(target["id"])
            results.append({"id": sid, "success": True})
        except Exception as e:
            results.append({"id": sid, "success": False, "error": str(e)})
    conn.commit()
    conn.close()
    return {"results": results}

@app.post("/api/strm/generate")
def generate_strm(request: StrmGenerateRequest, background_tasks: BackgroundTasks):
    strm_cfg = get_config("strm_config")
    base_url = strm_cfg.get("base_url") if strm_cfg else None
    if not base_url:
        raise HTTPException(status_code=400, detail="请先配置 STRM 基准 URL")
    client = get_alist_client()
    all_storages = client.list_storages()
    selected = [s for s in all_storages if s["id"] in request.storage_ids]
    if not selected:
        raise HTTPException(status_code=404, detail="未找到选中的挂载存储")
    task_id = str(uuid.uuid4())
    tasks_status[task_id] = {
        "status": "running", "progress": 0, "discovered": 0, "processed": 0,
        "message": "启动网盘深度拓扑扫描...", "log": []
    }
    log_buffer[task_id] = []
    task_stop_flags[task_id] = False

    def clean_empty_folders_by_rule(root_path: str, rule: int):
        if not os.path.exists(root_path):
            return
        deleted = 0
        for dirpath, dirnames, filenames in os.walk(root_path, topdown=False):
            if dirpath == root_path:
                continue
            should_delete = False
            if rule == 1:
                has_strm = any(f.endswith('.strm') for f in filenames)
                if not has_strm:
                    for root, dirs, files in os.walk(dirpath):
                        if any(f.endswith('.strm') for f in files):
                            has_strm = True
                            break
                if not has_strm:
                    should_delete = True
            elif rule == 2:
                if not filenames and not dirnames:
                    should_delete = True
            if should_delete:
                try:
                    shutil.rmtree(dirpath)
                    deleted += 1
                    add_log(task_id, f"[CLEAN] 删除空文件夹: {dirpath}")
                except Exception as e:
                    add_log(task_id, f"[ERROR] 删除失败 {dirpath}: {e}")
        if deleted > 0:
            add_log(task_id, f"[CLEAN] 共删除 {deleted} 个空文件夹")

    def run_worker():
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA foreign_keys=ON")
        progress_counter = {"discovered": 0, "processed": 0}
        total_storages = len(selected)
        save_roots = set()
        try:
            for idx, storage in enumerate(selected):
                storage_id = storage["id"]
                mount_path = storage.get("mount_path", "")
                c = conn.cursor()
                c.execute("SELECT id, strm_subdir, strm_save_root FROM mount_points WHERE storage_id=?", (storage_id,))
                row = c.fetchone()
                if not row:
                    add_log(task_id, f"[WARN] 存储 {storage_id} 缺失本地持久化映射，自动执行动态影子同步...")
                    strm_subdir = mount_path.rstrip('/').split('/')[-1] if mount_path else ""
                    save_root = strm_cfg.get("save_root", "/strm")
                    sync_mount_point(storage_id, "unknown", mount_path, strm_subdir, save_root, storage.get("remark",""))
                    c.execute("SELECT id, strm_subdir, strm_save_root FROM mount_points WHERE storage_id=?", (storage_id,))
                    row = c.fetchone()
                mp_id, strm_subdir, save_root = row
                save_roots.add(save_root)
                add_log(task_id, f"开始处理: {mount_path}, STRM子目录: {strm_subdir}, 保存根目录: {save_root}")
                try:
                    count = generate_strm_for_path(
                        client=client, strm_base_url=base_url, save_root=save_root,
                        strm_subdir=strm_subdir, mount_path=mount_path, alist_rel_path="",
                        task_id=task_id, conn=conn, progress_counter=progress_counter, mount_point_id=mp_id
                    )
                    c.execute("UPDATE mount_points SET last_generate_time=? WHERE id=?", (time.time(), mp_id))
                    conn.commit()
                    add_log(task_id, f"完成 {mount_path}，生成/更新 {count} 个STRM文件")
                except Exception as e:
                    add_log(task_id, f"扫描执行异常 {mount_path}: {str(e)}")
                tasks_status[task_id]["progress"] = int((idx + 1) / total_storages * 100)
                tasks_status[task_id]["discovered"] = progress_counter["discovered"]
                tasks_status[task_id]["processed"] = progress_counter["processed"]
                tasks_status[task_id]["log"] = log_buffer[task_id][-50:]
            # 清理空文件夹
            clean_enabled = get_config("clean_empty_folders")
            if clean_enabled:
                rule = get_config("clean_rule") or 1
                for root_path in save_roots:
                    if os.path.exists(root_path):
                        add_log(task_id, f"开始清理空文件夹（规则={rule}, 路径={root_path}）")
                        clean_empty_folders_by_rule(root_path, rule)
            tasks_status[task_id]["status"] = "completed"
            tasks_status[task_id]["message"] = f"同步处理完成！共扫描到 {progress_counter['discovered']} 视频，写入 {progress_counter['processed']} 映射。"
        finally:
            conn.close()
            task_stop_flags.pop(task_id, None)

    background_tasks.add_task(run_worker)
    return {"task_id": task_id}

@app.post("/api/storages/batch-webdav")
def batch_update_webdav(request: BatchWebdavRequest):
    if not request.new_base_url:
        raise HTTPException(status_code=400, detail="新基准URL不能为空")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    updated = 0
    for sid in request.storage_ids:
        c.execute("SELECT id, strm_subdir, strm_save_root FROM mount_points WHERE storage_id=?", (sid,))
        mp = c.fetchone()
        if not mp:
            continue
        mp_id, strm_subdir, save_root = mp
        c.execute("SELECT local_path, webdav_url FROM strm_files WHERE mount_point_id=?", (mp_id,))
        rows = c.fetchall()
        for local_path, old_url in rows:
            parts = old_url.split("/d/", 1)
            if len(parts) != 2:
                continue
            relative = parts[1]
            new_url = request.new_base_url.rstrip('/') + "/" + relative
            try:
                with open(local_path, 'w', encoding='utf-8') as f:
                    f.write(new_url)
                c.execute("UPDATE strm_files SET webdav_url=?, updated_at=? WHERE local_path=?", (new_url, time.time(), local_path))
                updated += 1
            except Exception as e:
                sys_logger.warning(f"重写文件失败 {local_path}: {e}")
    conn.commit()
    conn.close()
    return {"updated": updated}

@app.get("/api/storage/{storage_id}/check")
def check_storage_valid(storage_id: int):
    client = get_alist_client()
    all_storages = client.list_storages()
    target = next((s for s in all_storages if s.get("id") == storage_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="存储不存在")
    mount_path = target.get("mount_path", "")
    try:
        resp = client._request("POST", "/api/fs/list", data={"path": mount_path, "password": ""})
        if resp and isinstance(resp, dict) and "content" in resp:
            return {"valid": True}
        return {"valid": False, "error": "无法获取文件列表"}
    except Exception as e:
        return {"valid": False, "error": str(e)}

@app.post("/api/storage/{storage_id}/toggle")
def toggle_storage(storage_id: int, enabled: bool):
    client = get_alist_client()
    all_storages = client.list_storages()
    target = next((s for s in all_storages if s.get("id") == storage_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="存储不存在")
    target["disabled"] = not enabled
    client.update_storage(target)
    return {"success": True, "enabled": enabled}

@app.delete("/api/storages")
def batch_delete_storages(ids: List[int]):
    client = get_alist_client()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON")
    c = conn.cursor()
    results = []
    for sid in ids:
        c.execute("SELECT id, strm_save_root, strm_subdir FROM mount_points WHERE storage_id=?", (sid,))
        mp = c.fetchone()
        if mp:
            mp_id, save_root, subdir = mp
            c.execute("SELECT local_path FROM strm_files WHERE mount_point_id=?", (mp_id,))
            strm_paths = [row[0] for row in c.fetchall()]
            for local_path in strm_paths:
                if os.path.exists(local_path):
                    os.unlink(local_path)
            if subdir and save_root:
                target_dir = os.path.join(save_root, subdir)
                if os.path.exists(target_dir) and os.path.isdir(target_dir):
                    try:
                        current = target_dir
                        while current != save_root and os.path.exists(current) and os.path.isdir(current):
                            if not os.listdir(current):
                                os.rmdir(current)
                                current = os.path.dirname(current)
                            else:
                                break
                    except Exception as e:
                        sys_logger.warning(f"删除空目录失败 {target_dir}: {e}")
        c.execute("DELETE FROM mount_points WHERE storage_id=?", (sid,))
        try:
            client.delete_storage(sid)
            results.append({"id": sid, "success": True})
        except Exception as e:
            results.append({"id": sid, "success": False, "error": str(e)})
    conn.commit()
    conn.close()
    return {"results": results}

@app.get("/api/strm/task/{task_id}")
def get_strm_task_status(task_id: str):
    if task_id not in tasks_status:
        raise HTTPException(status_code=404, detail="任务不存在")
    tasks_status[task_id]["log"] = log_buffer.get(task_id, [])[-50:]
    return tasks_status[task_id]

@app.post("/api/strm/task/{task_id}/stop")
def stop_strm_task(task_id: str):
    if task_id not in tasks_status:
        raise HTTPException(status_code=404, detail="任务不存在")
    task_stop_flags[task_id] = True
    return {"success": True}

@app.get("/api/logs/system")
def get_system_logs(lines: int = 200):
    log_file = os.path.join(LOG_DIR, "system.log")
    if not os.path.exists(log_file):
        return {"logs": []}
    with open(log_file, "r", encoding="utf-8") as f:
        return {"logs": f.readlines()[-lines:]}

@app.get("/api/storage_defaults")
def get_storage_defaults_api():
    return get_storage_defaults()

@app.post("/api/storage_defaults")
def set_storage_defaults_api(request: dict):
    set_storage_defaults(
        driver=request.get("driver", "139Yun"),
        proxy_range=request.get("proxy_range", True),
        webdav_policy=request.get("webdav_policy", "302_redirect"),
        down_proxy_sign=request.get("down_proxy_sign", False)
    )
    return {"success": True}

@app.on_event("startup")
def startup_event():
    import threading
    threading.Timer(2, load_refresh_jobs).start()

@app.on_event("shutdown")
def shutdown_event():
    scheduler.shutdown()

@app.get("/", response_class=HTMLResponse)
async def root():
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>139Pan Manager</h1><p>前端文件缺失，请检查 static/index.html</p>", status_code=404)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5240)