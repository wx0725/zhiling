"""项目栏的存取接口：新建、列出、读回、改名、存工作流、清空、删除，以及非法输入被拒绝。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import server.app as appmod

client = TestClient(appmod.app)

WF = {"spec": "0.2", "id": "demo", "name": "演示", "intent": "", "inputs": {}, "outputs": {}, "nodes": [], "edges": []}


class Projects(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(appmod, "PROJECTS", Path(self.tmp.name) / "projects")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_create_list_get(self):
        self.assertEqual(client.get("/api/projects").json(), [])
        p = client.post("/api/projects", json={"name": "  第一个  ", "workflow": WF, "source": "examples", "file": "demo.workflow.json"}).json()
        self.assertEqual(p["name"], "第一个")
        self.assertEqual(p["source"], "examples")
        got = client.get(f"/api/projects/{p['id']}").json()
        self.assertEqual(got["workflow"], WF)
        self.assertEqual(got["dirty"], False)
        self.assertEqual([x["id"] for x in client.get("/api/projects").json()], [p["id"]])

    def test_empty_project_defaults(self):
        p = client.post("/api/projects", json={}).json()
        self.assertEqual(p["name"], "未命名项目")
        self.assertIsNone(p["workflow"])

    def test_update_rename_and_save_work(self):
        a = client.post("/api/projects", json={"name": "A"}).json()
        b = client.post("/api/projects", json={"name": "B"}).json()
        client.put(f"/api/projects/{a['id']}", json={"workflow": {**WF, "name": "改过"}, "source": "new", "dirty": True})
        # 最近改过的排在前面
        self.assertEqual([x["id"] for x in client.get("/api/projects").json()], [a["id"], b["id"]])
        client.put(f"/api/projects/{a['id']}", json={"name": "新名字"})
        got = client.get(f"/api/projects/{a['id']}").json()
        self.assertEqual((got["name"], got["workflow"]["name"], got["dirty"]), ("新名字", "改过", True))
        # 只改“有没有存进我保存的”：工作流不动
        client.put(f"/api/projects/{a['id']}", json={"dirty": False, "source": "saved"})
        got = client.get(f"/api/projects/{a['id']}").json()
        self.assertEqual((got["dirty"], got["source"], got["workflow"]["name"]), (False, "saved", "改过"))
        client.put(f"/api/projects/{a['id']}", json={"clear": True, "requirement": "重新想"})
        got = client.get(f"/api/projects/{a['id']}").json()
        self.assertEqual((got["workflow"], got["requirement"]), (None, "重新想"))

    def test_auto_name_follows_content_until_renamed(self):
        p = client.post("/api/projects", json={"requirement": "把网页总结成一句话，越短越好越短越好越短越好"}).json()
        self.assertEqual(p["name"], "把网页总结成一句话，越短越好越短越好越短越好"[:24])
        client.put(f"/api/projects/{p['id']}", json={"workflow": WF, "source": "draft"})
        self.assertEqual(client.get(f"/api/projects/{p['id']}").json()["name"], "演示")
        client.put(f"/api/projects/{p['id']}", json={"name": "我的"})
        client.put(f"/api/projects/{p['id']}", json={"workflow": {**WF, "name": "别的"}, "source": "draft"})
        self.assertEqual(client.get(f"/api/projects/{p['id']}").json()["name"], "我的")

    def test_older_save_does_not_overwrite_newer(self):
        p = client.post("/api/projects", json={}).json()
        url = f"/api/projects/{p['id']}"
        self.assertEqual(client.put(url, json={"workflow": {**WF, "name": "新"}, "source": "new", "rev": 20}).status_code, 200)
        self.assertEqual(client.put(url, json={"workflow": {**WF, "name": "旧"}, "source": "new", "rev": 10}).status_code, 409)
        self.assertEqual(client.put(url, json={"workflow": {**WF, "name": "新"}, "source": "new", "rev": 20}).status_code, 200)  # 同一次保存补发
        self.assertEqual(client.get(url).json()["workflow"]["name"], "新")
        client.put(url, json={"name": "改名不带编号"})  # 改名不影响编号
        self.assertEqual(client.put(url, json={"workflow": WF, "source": "new", "rev": 15}).status_code, 409)

    def test_concurrent_writes_keep_file_valid(self):
        import threading
        p = client.post("/api/projects", json={}).json()
        url = f"/api/projects/{p['id']}"
        codes = []

        def put(i):
            codes.append(client.put(url, json={"workflow": {**WF, "name": f"第{i}"}, "source": "new", "rev": 100 + i}).status_code)

        ts = [threading.Thread(target=put, args=(i,)) for i in range(12)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertTrue(all(c in (200, 409) for c in codes), codes)
        got = client.get(url).json()
        self.assertEqual(got["rev"], 111)  # 编号最大的那次留下
        self.assertEqual(got["workflow"]["name"], "第11")
        self.assertEqual(list(appmod.PROJECTS.glob("*.tmp")), [])

    def test_delete(self):
        p = client.post("/api/projects", json={}).json()
        self.assertEqual(client.delete(f"/api/projects/{p['id']}").status_code, 200)
        self.assertEqual(client.get(f"/api/projects/{p['id']}").status_code, 404)
        self.assertEqual(client.delete(f"/api/projects/{p['id']}").status_code, 404)
        self.assertEqual(client.get("/api/projects").json(), [])

    def test_bad_input_rejected(self):
        self.assertIn(client.get("/api/projects/..%2Fsecret").status_code, (400, 404))
        self.assertEqual(client.get("/api/projects/p_ABC").status_code, 400)
        self.assertEqual(client.put("/api/projects/p_000000", json={"name": "x"}).status_code, 404)
        p = client.post("/api/projects", json={}).json()
        self.assertEqual(client.put(f"/api/projects/{p['id']}", json={"name": "   "}).status_code, 400)
        self.assertEqual(client.put(f"/api/projects/{p['id']}", json={"workflow": WF, "source": "other"}).status_code, 400)
        self.assertEqual(client.put(f"/api/projects/{p['id']}", json={"workflow": WF, "file": "../x.json"}).status_code, 400)
        self.assertEqual(client.post("/api/projects", json={"name": ""}).status_code, 400)

    def test_broken_file_skipped(self):
        p = client.post("/api/projects", json={"name": "好的"}).json()
        (appmod.PROJECTS / "p_broken01.project.json").write_text("{坏", encoding="utf-8")
        (appmod.PROJECTS / "p_broken02.project.json").write_text("[1]", encoding="utf-8")
        self.assertEqual([x["id"] for x in client.get("/api/projects").json()], [p["id"]])


if __name__ == "__main__":
    unittest.main()
