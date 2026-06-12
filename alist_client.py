import requests
from typing import List, Dict, Any, Optional

class AlistClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.token = None
        self._ensure_login()

    def _ensure_login(self):
        if not self.token:
            self._login()

    def _login(self):
        resp = requests.post(
            f"{self.base_url}/api/auth/login",
            json={"username": self.username, "password": self.password}
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200:
            raise Exception(f"AList登录失败: {data.get('message')}")
        self.token = data["data"]["token"]

    def _request(self, method: str, path: str, data=None, json_data=None) -> Dict:
        self._ensure_login()
        headers = {"Authorization": self.token}
        url = f"{self.base_url}{path}"
        
        if json_data is not None:
            headers["Content-Type"] = "application/json"
            resp = requests.request(method, url, headers=headers, json=json_data)
        else:
            resp = requests.request(method, url, headers=headers, data=data)

        # 401 过期自动刷新并重试
        if resp.status_code == 401:
            self._login()
            headers["Authorization"] = self.token
            if json_data is not None:
                resp = requests.request(method, url, headers=headers, json=json_data)
            else:
                resp = requests.request(method, url, headers=headers, data=data)

        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 200:
            raise Exception(f"AList API错误: {result.get('message')}")
        return result.get("data")

    def list_storages(self) -> List[Dict]:
        # 增加分页参数，避免挂载点过多时遗漏
        data = self._request("GET", "/api/admin/storage/list?page=1&size=1000")
        items = []
        if isinstance(data, dict):
            if "content" in data:
                items = data["content"]
            elif "items" in data:
                items = data["items"]
        elif isinstance(data, list):
            items = data
            
        result = []
        for item in items:
            if isinstance(item, dict) and "id" in item:
                result.append(item)
        return result

    def create_storage(self, storage: Dict) -> Dict:
        return self._request("POST", "/api/admin/storage/create", json_data=storage)

    def update_storage(self, storage: Dict) -> Dict:
        return self._request("POST", "/api/admin/storage/update", json_data=storage)

    def delete_storage(self, id: int) -> Dict:
        return self._request("POST", f"/api/admin/storage/delete?id={id}")

    def enable_storage(self, id: int) -> Dict:
        return self._request("POST", "/api/admin/storage/enable", json_data={"id": id})

    def get_storage(self, id: int) -> Optional[Dict]:
        return self._request("GET", f"/api/admin/storage/get?id={id}")