"""Comandos: python -m lockapp setup | run | relay-test."""
import argparse
import getpass
import logging
import os
from pathlib import Path
import sys
import time

from .core import Relay, Store, Vision
from .server import create_app


def main():
    parser = argparse.ArgumentParser(description="Fechadura Digital (Orange Pi 3 LTS)")
    parser.add_argument("--data-dir", default=os.getenv(
        "LOCK_DATA_DIR", str(Path.home() / ".local/share/fechadura-digital")))
    parser.add_argument("--models-dir", default=os.getenv(
        "LOCK_MODELS_DIR", str(Path(__file__).resolve().parent.parent / "models")))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("command", choices=["setup", "run", "relay-test"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = Store(args.data_dir)

    if args.command == "setup":
        first = getpass.getpass("Defina a senha de administrador (mínimo 12 caracteres): ")
        second = getpass.getpass("Repita a senha: ")
        if first != second:
            parser.error("Senhas diferentes")
        try:
            store.set_password(first)
        except ValueError as exc:
            parser.error(str(exc))
        print("Senha configurada no banco local. Não há senha padrão.")
        return

    mode = os.getenv("LOCK_RELAY_MODE", "mock")
    chip = os.getenv("LOCK_GPIO_CHIP", "/dev/gpiochip1")
    offset = int(os.getenv("LOCK_GPIO_OFFSET", "73"))
    name = os.getenv("LOCK_GPIO_NAME", "PC9")

    if args.command == "relay-test" and mode != "gpio":
        parser.error("Configure LOCK_RELAY_MODE=gpio somente após conferir cabeamento")
    if args.command == "run" and not store.has_password:
        parser.error("Cadastre primeiro uma senha: python -m lockapp setup")

    relay = Relay(mode, chip, offset, name)
    if args.command == "relay-test":
        try:
            print("Pulso de 0,5 segundo no relé configurado.")
            relay.pulse(0.5)
            time.sleep(0.5)
            relay.tick()
        finally:
            relay.close()
        return

    vision = Vision(store, relay, args.models_dir)
    try:
        vision.start()
        app = create_app(store, vision)
        from waitress import serve
        print(f"Interface local: http://127.0.0.1:{args.port}")
        serve(app, host="127.0.0.1", port=args.port, threads=8,
              ident="FechaduraDigital")
    except KeyboardInterrupt:
        pass
    finally:
        vision.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logging.exception("Inicialização falhou. Acesso permanece bloqueado.")
        sys.exit(1)
