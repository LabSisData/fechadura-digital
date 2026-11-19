"""Núcleo local: dados biométricos, controle físico e captura de câmera."""
import base64
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid

import cv2
import numpy as np
from cryptography.fernet import Fernet

LOG = logging.getLogger(__name__)
DEFAULTS = {
    "camera": {"device": 0, "width": 640, "height": 480, "fps": 12, "flip": False},
    "access": {"min_score": 0.58, "confirm_frames": 5, "pulse_seconds": 1.5,
               "cooldown_seconds": 15},
}
MODEL_FILES = ("face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx")


def normalized(values):
    arr = np.asarray(values, dtype=np.float32).reshape(-1)
    if arr.size != 128 or not np.all(np.isfinite(arr)):
        raise ValueError("Vetor facial inválido")
    norm = float(np.linalg.norm(arr))
    if norm < 1e-6:
        raise ValueError("Vetor facial vazio")
    return arr / norm


def password_hash(password):
    if len(password) < 12 or len(password) > 256:
        raise ValueError("A senha precisa ter entre 12 e 256 caracteres")
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(key).decode()


def check_password(password, encoded):
    try:
        method, salt, value = encoded.split("$")
        if method != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode("utf-8"),
                                   salt=base64.b64decode(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(candidate, base64.b64decode(value))
    except (ValueError, TypeError):
        return False


def _secret(path, generate):
    if not path.exists():
        path.write_bytes(generate())
        os.chmod(path, 0o600)
    if path.is_symlink():
        raise RuntimeError("Arquivo de segredo não pode ser link simbólico")
    return path.read_bytes()


class Store:
    def __init__(self, folder):
        self.folder = Path(folder).expanduser().resolve()
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.folder, 0o700)
        self.lock = threading.RLock()
        self.session_key = _secret(self.folder / "session.key", lambda: secrets.token_bytes(32))
        self.fernet = Fernet(_secret(self.folder / "biometric.key", Fernet.generate_key))
        self.db = sqlite3.connect(self.folder / "lock.sqlite3", check_same_thread=False)
        os.chmod(self.folder / "lock.sqlite3", 0o600)
        self.db.row_factory = sqlite3.Row
        with self.lock, self.db:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("""CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS persons (
                id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                enabled INTEGER NOT NULL DEFAULT 1, embedding BLOB NOT NULL,
                created_at INTEGER NOT NULL)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL,
                event TEXT NOT NULL, person_id TEXT)""")
            self.db.execute("DELETE FROM audit WHERE at < ?", (int(time.time()) - 7 * 86400,))
            if self._meta("settings") is None:
                self._set_meta("settings", json.dumps(DEFAULTS))

    def _meta(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def _set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    @property
    def has_password(self):
        with self.lock:
            return self._meta("admin_password") is not None

    def set_password(self, password):
        encoded = password_hash(password)
        with self.lock, self.db:
            self._set_meta("admin_password", encoded)
        self.audit("password_changed")

    def verify_password(self, password):
        with self.lock:
            encoded = self._meta("admin_password")
        return bool(encoded and check_password(password, encoded))

    def settings(self):
        with self.lock:
            return json.loads(self._meta("settings"))

    def set_camera(self, values):
        camera = {
            "device": int(values["device"]), "width": int(values["width"]),
            "height": int(values["height"]), "fps": int(values["fps"]),
            "flip": bool(values["flip"]),
        }
        if not (0 <= camera["device"] <= 9 and
                camera["width"] in (320, 640, 800, 1280) and
                camera["height"] in (240, 480, 600, 720) and
                1 <= camera["fps"] <= 25):
            raise ValueError("Configuração de câmera fora dos limites")
        with self.lock, self.db:
            current = self.settings()
            current["camera"] = camera
            self._set_meta("settings", json.dumps(current))

    def list_people(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT id, name, enabled, created_at FROM persons ORDER BY name").fetchall()
        return [dict(row) for row in rows]

    def templates(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT id, name, embedding FROM persons WHERE enabled=1").fetchall()
            return [(row["id"], row["name"],
                     normalized(json.loads(self.fernet.decrypt(row["embedding"]).decode())))
                    for row in rows]

    def add_person(self, name, embedding):
        name = " ".join(name.split())
        if not (2 <= len(name) <= 70) or any(ord(c) < 32 for c in name):
            raise ValueError("Nome deve ter entre 2 e 70 caracteres")
        person_id = str(uuid.uuid4())
        payload = self.fernet.encrypt(json.dumps(normalized(embedding).tolist()).encode())
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO persons(id,name,embedding,created_at) VALUES(?,?,?,?)",
                (person_id, name, payload, int(time.time())))
        self.audit("enrolled", person_id)
        return person_id

    def set_enabled(self, person_id, enabled):
        with self.lock, self.db:
            cursor = self.db.execute("UPDATE persons SET enabled=? WHERE id=?",
                                     (int(bool(enabled)), person_id))
            if cursor.rowcount != 1:
                raise ValueError("Pessoa não encontrada")
        self.audit("permission_changed", person_id)

    def delete_person(self, person_id):
        with self.lock, self.db:
            cursor = self.db.execute("DELETE FROM persons WHERE id=?", (person_id,))
            if cursor.rowcount != 1:
                raise ValueError("Pessoa não encontrada")
        self.audit("deleted", person_id)

    def audit(self, event, person_id=None):
        with self.lock, self.db:
            self.db.execute("INSERT INTO audit(at,event,person_id) VALUES(?,?,?)",
                            (int(time.time()), event, person_id))

    def recent_audit(self):
        with self.lock:
            return [dict(row) for row in self.db.execute(
                "SELECT at,event,person_id FROM audit ORDER BY id DESC LIMIT 30")]


class Relay:
    """Nunca energiza no início. GPIO real somente por opção explícita no ambiente."""
    def __init__(self, mode="mock", chip="/dev/gpiochip1", offset=73, expected_name="PC9"):
        self.lock = threading.RLock()
        self.request = None
        self.until = 0.0
        self.offset = int(offset)
        self.mode = mode
        if mode == "mock":
            LOG.warning("RELÉ EM SIMULAÇÃO: nenhuma saída física será acionada")
        elif mode == "gpio":
            import gpiod
            from gpiod.line import Direction, Value
            self._inactive, self._active = Value.INACTIVE, Value.ACTIVE
            # Impede acionar outro pino caso o kernel enumere gpiochips de modo diferente.
            with gpiod.Chip(chip) as c:
                info = c.get_line_info(self.offset)
                if info.name != expected_name:
                    raise RuntimeError(
                        f"Pino não validado: {chip} offset {self.offset} = {info.name!r}; "
                        f"esperado {expected_name}. Não ativar sem revisar o pinout.")
            self.request = gpiod.request_lines(
                chip, consumer="fechadura-digital",
                config={self.offset: gpiod.LineSettings(
                    direction=Direction.OUTPUT, active_low=True,
                    output_value=Value.INACTIVE)})
        else:
            raise ValueError("LOCK_RELAY_MODE deve ser mock ou gpio")

    def pulse(self, seconds):
        with self.lock:
            if self.until > time.monotonic():
                return
            if self.request:
                self.request.set_value(self.offset, self._active)
            else:
                LOG.warning("SIMULAÇÃO de abertura por %.2f segundos", seconds)
            self.until = time.monotonic() + float(seconds)

    def tick(self):
        with self.lock:
            if self.until and time.monotonic() >= self.until:
                self.off()

    def off(self):
        with self.lock:
            if self.request:
                self.request.set_value(self.offset, self._inactive)
            self.until = 0.0

    def close(self):
        with self.lock:
            try:
                self.off()
            finally:
                if self.request:
                    self.request.release()
                    self.request = None


class Vision:
    def __init__(self, store, relay, model_dir):
        self.store, self.relay = store, relay
        self.model_dir = Path(model_dir)
        self.mu = threading.RLock()
        self.status = "Inicializando câmera"
        self.jpeg = None
        self.enroll = None
        self.enroll_result = ""
        self.restart = threading.Event()
        self.stop = threading.Event()
        self.thread = None
        self.templates = []
        self._subject = None
        self._count = 0
        self._latched = False
        self._last_face = 0
        self._last_unlock = -1e12
        self.refresh()

    def refresh(self):
        templates = self.store.templates()
        with self.mu:
            self.templates = templates
            self._subject = None
            self._count = 0

    def start(self):
        if self.thread is not None:
            raise RuntimeError("Captura já iniciada")
        for name in MODEL_FILES:
            if not (self.model_dir / name).is_file():
                raise RuntimeError("Modelo ausente: " + name + ". Execute download_models.py")
        self.thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self.thread.start()

    def begin_enroll(self, name):
        name = " ".join(name.split())
        if not 2 <= len(name) <= 70:
            raise ValueError("Nome inválido")
        with self.mu:
            if self.enroll is not None:
                raise ValueError("Já existe um cadastro em andamento")
            if any(p["name"].casefold() == name.casefold() for p in self.store.list_people()):
                raise ValueError("Nome já cadastrado")
            self.enroll = {"name": name, "samples": [], "next": 0.0,
                           "deadline": time.monotonic() + 40}
            self.enroll_result = "Aguarde: posicione um rosto diante da câmera"

    def snapshot(self):
        with self.mu:
            progress = (len(self.enroll["samples"]) if self.enroll else None)
            return {"status": self.status, "enrollment": progress,
                    "enrollment_result": self.enroll_result, "relay_mode": self.relay.mode}

    def frame(self):
        with self.mu:
            return self.jpeg

    def _run(self):
        cv2.setNumThreads(1)
        detector = cv2.FaceDetectorYN.create(
            str(self.model_dir / MODEL_FILES[0]), "", (320, 240), 0.9, 0.3, 5)
        recognizer = cv2.FaceRecognizerSF.create(str(self.model_dir / MODEL_FILES[1]), "")
        cap = None
        last_open_attempt = 0
        try:
            while not self.stop.is_set():
                self.relay.tick()
                settings = self.store.settings()
                cc = settings["camera"]
                if self.restart.is_set():
                    self.restart.clear()
                    if cap:
                        cap.release()
                        cap = None
                    with self.mu:
                        self._subject, self._count, self._latched = None, 0, False
                if cap is None:
                    if time.monotonic() - last_open_attempt < 2:
                        self.stop.wait(0.1)
                        continue
                    last_open_attempt = time.monotonic()
                    cap = cv2.VideoCapture(cc["device"], cv2.CAP_V4L2)
                    if not cap.isOpened():
                        cap.release()
                        cap = None
                        with self.mu:
                            self.status = "Câmera indisponível: verifique /dev/video*"
                        continue
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cc["width"])
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cc["height"])
                    cap.set(cv2.CAP_PROP_FPS, cc["fps"])
                ok, frame = cap.read()
                if not ok:
                    cap.release()
                    cap = None
                    self.relay.off()
                    with self.mu:
                        self.status = "Falha de captura: reconectando câmera"
                    continue
                if cc["flip"]:
                    frame = cv2.flip(frame, 1)
                view = cv2.resize(frame, (320, 240))
                _, faces = detector.detect(view)
                faces = [] if faces is None else faces
                now = time.monotonic()
                with self.mu:
                    enrolling = self.enroll is not None
                if len(faces) != 1:
                    with self.mu:
                        self._subject, self._count = None, 0
                        self.status = ("Procurando rostos" if len(faces) == 0
                                       else "Uma pessoa por vez")
                        if now - self._last_face > 2:
                            self._latched = False
                        if self.enroll and now > self.enroll["deadline"]:
                            self.enroll = None
                            self.enroll_result = "Tempo de cadastro esgotado"
                else:
                    face = faces[0]
                    self._last_face = now
                    # Não identificar rostos pequenos: baixa qualidade eleva falso positivo.
                    if face[2] < 70 or face[3] < 70:
                        with self.mu:
                            self._subject, self._count = None, 0
                            self.status = "Aproxime o rosto da câmera"
                    else:
                        aligned = recognizer.alignCrop(view, face)
                        vector = normalized(recognizer.feature(aligned))
                        x, y, w, h = [int(v) for v in face[:4]]
                        scale_x, scale_y = frame.shape[1] / 320, frame.shape[0] / 240
                        cv2.rectangle(frame, (int(x*scale_x), int(y*scale_y)),
                                      (int((x+w)*scale_x), int((y+h)*scale_y)),
                                      (200, 220, 200), 2)
                        if enrolling:
                            self._do_enroll(vector, now, settings["access"]["min_score"])
                        else:
                            self._recognize(vector, now, settings["access"])
                ok_jpg, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok_jpg:
                    with self.mu:
                        self.jpeg = jpg.tobytes()
                self.stop.wait(max(0.015, 1 / cc["fps"]))
        except Exception:
            LOG.exception("Falha no processador facial; saída deve permanecer fechada")
            with self.mu:
                self.status = "Falha interna: acesso bloqueado"
        finally:
            if cap:
                cap.release()
            self.relay.close()

    def _do_enroll(self, vector, now, threshold):
        with self.mu:
            req = self.enroll
            if not req:
                return
            if now > req["deadline"]:
                self.enroll = None
                self.enroll_result = "Tempo de cadastro esgotado"
                return
            if now < req["next"]:
                return
            for _, _, known in self.templates:
                if float(np.dot(vector, known)) >= threshold:
                    self.enroll = None
                    self.enroll_result = "Rosto já pertence a outra pessoa cadastrada"
                    self.status = self.enroll_result
                    return
            req["samples"].append(vector)
            req["next"] = now + 0.6
            self.status = f"Cadastrando: amostra {len(req['samples'])}/5"
            if len(req["samples"]) == 5:
                try:
                    mean = normalized(np.mean(req["samples"], axis=0))
                    self.store.add_person(req["name"], mean)
                    self.enroll_result = "Cadastro concluído: " + req["name"]
                    self.status = self.enroll_result
                    self.refresh()
                except (ValueError, sqlite3.IntegrityError) as exc:
                    self.enroll_result = "Falha no cadastro: " + str(exc)
                finally:
                    self.enroll = None

    def _recognize(self, vector, now, access):
        with self.mu:
            scored = sorted(
                ((float(np.dot(vector, v)), ident, name)
                 for ident, name, v in self.templates), reverse=True)
            if not scored or scored[0][0] < access["min_score"] or (
                len(scored) > 1 and scored[0][0] - scored[1][0] < 0.06
            ):
                self._subject, self._count = None, 0
                self.status = "Rosto não autorizado"
                return
            _, ident, name = scored[0]
            if self._subject != ident:
                self._subject, self._count = ident, 0
            self._count += 1
            self.status = "Identificando " + name if self._count < access["confirm_frames"] else (
                "Acesso permitido: " + name)
            if (self._count >= access["confirm_frames"] and not self._latched
                    and now - self._last_unlock >= access["cooldown_seconds"]
                    and self.enroll is None):
                # Evitar permissão revogada durante a sequência de frames.
                if not any(p["id"] == ident and p["enabled"] for p in self.store.list_people()):
                    self._subject, self._count = None, 0
                    self.status = "Permissão revogada"
                    return
                self.relay.pulse(access["pulse_seconds"])
                self.store.audit("access_granted", ident)
                self._last_unlock = now
                self._latched = True

    def shutdown(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
        self.relay.close()
