"""Testes que não requerem câmera nem GPIO."""
import os
import tempfile
import threading
import unittest

import numpy as np

from lockapp.core import Relay, Store, Vision, check_password, normalized, password_hash
from lockapp.server import create_app


def vector(index=0):
    arr = np.zeros(128, dtype=np.float32)
    arr[index] = 1.0
    return arr


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_password_hash_and_wrong_password(self):
        self.assertFalse(self.store.has_password)
        self.store.set_password("Senha bastante forte 123")
        self.assertTrue(self.store.verify_password("Senha bastante forte 123"))
        self.assertFalse(self.store.verify_password("Senha incorreta"))
        self.assertNotIn("Senha bastante forte 123",
                         self.store.db.execute("SELECT value FROM meta WHERE key='admin_password'")
                         .fetchone()[0])

    def test_invalid_password(self):
        with self.assertRaises(ValueError):
            password_hash("curta")
        self.assertFalse(check_password("qualquer", "desconhecido"))

    def test_biometrics_encrypted_and_revocation(self):
        identifier = self.store.add_person("Pessoa Um", vector())
        saved = self.store.db.execute("SELECT embedding FROM persons").fetchone()[0]
        self.assertNotIn(b"[1.0,", saved)
        self.assertEqual(self.store.templates()[0][0], identifier)
        self.store.set_enabled(identifier, False)
        self.assertEqual(self.store.templates(), [])
        self.store.set_enabled(identifier, True)
        self.store.delete_person(identifier)
        self.assertEqual(self.store.list_people(), [])

    def test_reject_bad_camera_and_embeddings(self):
        with self.assertRaises(ValueError):
            normalized(np.zeros(128))
        with self.assertRaises(ValueError):
            self.store.set_camera(dict(device=0, width=999, height=480, fps=12, flip=False))
        self.assertEqual(self.store.settings()["camera"]["width"], 640)

    def test_data_directory_not_world_readable(self):
        mode = os.stat(self.temp.name).st_mode & 0o777
        self.assertEqual(mode, 0o700)


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.relay = Relay("mock")
        self.vision = Vision(self.store, self.relay, self.temp.name)

    def tearDown(self):
        self.relay.close()
        self.store.db.close()
        self.temp.cleanup()

    def test_confirm_frames_and_refractory_latch(self):
        self.store.add_person("Ana", vector())
        self.vision.refresh()
        cfg = self.store.settings()["access"]
        for i in range(cfg["confirm_frames"] - 1):
            self.vision._recognize(vector(), float(i + 1), cfg)
        self.assertEqual(self.relay.until, 0)
        self.vision._recognize(vector(), 10, cfg)
        self.assertGreater(self.relay.until, 0)
        previous = self.vision._last_unlock
        self.vision._recognize(vector(), 40, cfg)
        self.assertEqual(self.vision._last_unlock, previous)

    def test_reject_unknown_person(self):
        self.store.add_person("Ana", vector(0))
        self.vision.refresh()
        self.vision._recognize(vector(1), 10, self.store.settings()["access"])
        self.assertEqual(self.vision.snapshot()["status"], "Rosto não autorizado")
        self.assertEqual(self.relay.until, 0)


class DummyVision:
    def __init__(self):
        self.stop = threading.Event()
        self.restart = threading.Event()
        self.relay = Relay("mock")

    def snapshot(self):
        return {"status": "Procurando rostos", "enrollment": None,
                "enrollment_result": "", "relay_mode": "mock"}

    def frame(self):
        return None

    def refresh(self):
        pass

    def begin_enroll(self, name):
        pass


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.store.set_password("Senha Segura 12345")
        self.vision = DummyVision()
        self.app = create_app(self.store, self.vision)
        self.app.testing = True
        self.client = self.app.test_client()

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_password_login_and_csrf(self):
        self.assertEqual(self.client.get("/settings").status_code, 302)
        self.assertEqual(self.client.post("/login", data={"password": "Senha Segura 12345"}).status_code, 403)
        self.client.get("/login")
        with self.client.session_transaction() as sess:
            token = sess["csrf"]
        response = self.client.post("/login", data={
            "password": "Senha Segura 12345", "_csrf": token})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/settings").status_code, 200)
        self.assertEqual(self.client.post("/restart-camera", data={"_csrf": "errado"}).status_code, 403)

    def test_loopback_main_and_csp(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Procurando rostos", response.data)
        self.assertIn("frame-ancestors", response.headers["Content-Security-Policy"])


if __name__ == "__main__":
    unittest.main()
