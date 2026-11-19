"""Baixa os dois modelos oficiais do OpenCV Zoo e verifica SHA-256."""
from hashlib import sha256
from pathlib import Path
import urllib.request

BASE = ("https://huggingface.co/opencv/opencv_zoo/resolve/"
        "d4938dfc9d4ec5d098bfa33e98b3f3345a236586/models/")
MODELS = {
    "face_detection_yunet/face_detection_yunet_2023mar.onnx":
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    "face_recognition_sface/face_recognition_sface_2021dec.onnx":
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
}


def main():
    target = Path(__file__).resolve().parent.parent / "models"
    target.mkdir(exist_ok=True)
    for remote, digest in MODELS.items():
        name = remote.rsplit("/", 1)[-1]
        dest = target / name
        if dest.exists() and sha256(dest.read_bytes()).hexdigest() == digest:
            print(name, "já verificado")
            continue
        part = dest.with_suffix(".download")
        print("Baixando", name, "...")
        try:
            h = sha256()
            request = urllib.request.Request(BASE + remote, headers={
                "User-Agent": "fechadura-digital/1.0"})
            with urllib.request.urlopen(request, timeout=90) as response, part.open("wb") as out:
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > 60 * 1024 * 1024:
                        raise RuntimeError("Modelo excede o tamanho esperado")
                    h.update(chunk)
                    out.write(chunk)
            if h.hexdigest() != digest:
                raise RuntimeError("SHA-256 incorreto: download não confiável")
            part.replace(dest)
            print("OK:", name, size, "bytes")
        finally:
            part.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
