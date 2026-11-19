# Fechadura Digital — Orange Pi 3 LTS

Projeto de reconhecimento facial local em Python para **Orange Pi 3 LTS ARM64**, webcam USB e relé. A interface principal mostra somente a câmera, o status do reconhecimento e um botão Configurações. As configurações exigem uma senha administrativa única, definida pelo operador (sem senha padrão).

> **Estado: protótipo integrado, ainda não homologado para produção física.** Por padrão o relé é SIMULADO: nenhum pino é acionado. Câmeras comuns não impedem ataques com foto/vídeo. Não use reconhecimento facial como único fator de segurança em porta real; veja a seção Segurança e produção.

## Funcionalidades

- Processa um único rosto por vez; quando aparecem dois ou mais, a identificação é suspensa.
- Detector YuNet e reconhecedor SFace (OpenCV DNN), sem transmissão para nuvem.
- Cadastro automático em cinco amostras e reconhecimento disponível logo após o cadastro.
- Liberação apenas após cinco confirmações consecutivas, similaridade conservadora, cooldown e saída do rosto do enquadramento antes de nova liberação.
- Controle de pessoas autorizadas, bloqueio, reautorização e exclusão.
- Ajustes de índice da câmera, largura, altura, FPS, espelhamento e botão Reiniciar câmera.
- Senha protegida com scrypt, sessão de 15 minutos, limite de tentativas e CSRF.
- Vetores faciais criptografados com Fernet em banco SQLite local. Não armazena fotos.
- Saída de relé desenergizada fora de pulso de 1,5 segundo, com validação do pino GPIO.
- Serviço systemd, testes automáticos e interface local restrita a 127.0.0.1.

## 1. Preparar o Orange Pi

Use Ubuntu ou Debian ARM64, preferencialmente Python 3.11 ou 3.12, webcam V4L2 e acesso à internet para baixar os modelos (~39 MB).

~~~bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip \
  libgl1 libglib2.0-0 v4l-utils gpiod
python3 --version
uname -m
~~~

A arquitetura deve ser aarch64. Evite alterar o Python padrão do sistema.

## 2. Clonar e criar ambiente virtual

~~~bash
git clone https://github.com/LabSisData/fechadura-digital.git
cd fechadura-digital
# Antes do merge do PR, ative a branch deste projeto:
git switch feat/orangepi-face-lock
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --only-binary=:all: -r requirements.txt
~~~

O argumento --only-binary=:all: evita compilar OpenCV na placa; se não houver wheel para a arquitetura, ocorrerá falha explícita. Para clonar o repositório privado, autentique-se no GitHub.

## 3. Conferir a câmera

~~~bash
ls -l /dev/video*
v4l2-ctl --list-devices
sudo usermod -aG video "$USER"
~~~

Encerre a sessão e entre novamente para carregar o grupo video. O índice inicial é 0 (geralmente /dev/video0); ele pode ser alterado no painel. Uma webcam USB pode fornecer mais de um dispositivo de vídeo: escolha o de captura real.

## 4. Baixar modelos e definir a senha

~~~bash
source .venv/bin/activate
python scripts/download_models.py
python -m lockapp setup
~~~

O script usa modelos do OpenCV Zoo, obtidos em uma revisão específica, e verifica SHA-256. A senha deve ter pelo menos 12 caracteres. Para alterá-la, repita o comando setup no terminal local; não existe senha padrão nem função de recuperar a senha pela internet.

## 5. Rodar e cadastrar pessoas

~~~bash
python -m lockapp run
~~~

Abra no próprio Orange Pi, em um navegador:

**http://127.0.0.1:8765**

A tela principal é a câmera com o status abaixo dela. Clique em Configurações, insira a senha, ajuste os controles da câmera e clique em Salvar câmera ou Reiniciar câmera. Para cadastrar, escreva o nome e clique em Iniciar cadastro, com somente um rosto bem visível e iluminado diante da câmera por alguns segundos. O programa captura cinco amostras e registra um vetor facial criptografado. Após concluir, a identificação fica disponível imediatamente. Pessoas autorizadas podem ser bloqueadas, reautorizadas e excluídas. O painel também permite ajustar os parâmetros de confirmação, similaridade mínima, duração do pulso e intervalo entre liberações, além de trocar a senha exigindo a senha anterior.

O reconhecimento é executado localmente em imagem de 320×240 para reduzir custo de CPU. O vídeo mostrado mantém a resolução de captura configurada. FPS e precisão dependem da câmera, luz e desempenho real do Orange Pi.

Modo quiosque opcional, na sessão gráfica local:

~~~bash
chromium --kiosk --no-first-run http://127.0.0.1:8765
~~~

Em algumas distribuições, o comando será chromium-browser.

Se o Orange Pi iniciar em **desktop gráfico com login automático já configurado**, pode iniciar o navegador em modo quiosque ao abrir a sessão gráfica. Exemplo para ambientes que respeitam XDG Autostart:

~~~bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/fechadura-kiosk.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Fechadura Digital
Exec=chromium --kiosk --no-first-run http://127.0.0.1:8765
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
~~~

Configure o quiosque em um usuário dedicado sem acesso administrativo ao sistema, desabilitando navegação não autorizada e protegendo o acesso físico ao terminal. O serviço Python é independente do navegador.

## 6. Conectar o relé — com testes físicos prévios

**Pino escolhido:** pino físico **7** do header de 26 pinos, sinal **PC9**, nível lógico **3,3 V**. O pino físico **6** é GND. Para bancos Allwinner, PC9 corresponde ao offset **73** (banco C, 64 + 9), mas o número do chip Linux pode variar conforme imagem/kernel. **Não confie no exemplo de gpiochip1 sem verificar na sua placa.**

~~~bash
gpioinfo | less
gpioinfo | grep -i -C 3 PC9
ls -l /dev/gpiochip*
~~~

Inspecione e confirme nome PC9, offset, chip correto e ausência de conflito de uso. O programa se recusa a acionar outra linha quando o nome exposto pelo kernel não corresponde a PC9. Este projeto usa libgpiod v2; não use números BCM ou wPi do wiringOP.

Use **módulo de relé ou driver eletricamente compatível com entrada de 3,3 V e acionamento ativo em nível baixo**. Um pull-up físico externo deve manter o relé desligado no boot e quando o processo liberar o GPIO. A fonte do módulo/fechadura deve ser adequada e isolada; **não ligue bobina, solenóide ou tensão de rede diretamente ao pino do Orange Pi**. Confirme contatos COM/NO/NC e o comportamento da fechadura com profissional habilitado.

Instale suporte GPIO no mesmo venv:

~~~bash
source .venv/bin/activate
python -m pip install --only-binary=:all: -r requirements-gpio.txt
~~~

A conta do processo deve ter permissão ao arquivo /dev/gpiochipN. Configure regras udev/grupo apropriados ao sistema, sem executar o servidor web como root.

Após conferir com multímetro, faça o teste de 0,5 segundo **sem a fechadura real ligada**. Troque o chip e offset somente se confirmados por gpioinfo:

~~~bash
LOCK_RELAY_MODE=gpio LOCK_GPIO_CHIP=/dev/gpiochip1 \
LOCK_GPIO_OFFSET=73 LOCK_GPIO_NAME=PC9 \
python -m lockapp relay-test
~~~

Só depois de validar com carga inofensiva e supervisão:

~~~bash
LOCK_RELAY_MODE=gpio LOCK_GPIO_CHIP=/dev/gpiochip1 \
LOCK_GPIO_OFFSET=73 LOCK_GPIO_NAME=PC9 \
python -m lockapp run
~~~

Em modo mock (padrão), uma autorização **não** toca o GPIO. Em modo gpio, o relé começa no estado inativo, é ativado durante 1,5 segundo, depois volta ao inativo. Em produção use também watchdog/timer de hardware que desconecte o atuador em falhas ou travamentos; um processo travado pode manter uma saída elétrica ligada.

## 7. Iniciar automaticamente com systemd

Execute na pasta do projeto, depois de configurar a senha no usuário que executará o serviço:

~~~bash
APP_DIR="$(pwd)"
APP_USER="$(id -un)"
sed -e "s|USUARIO|$APP_USER|g" \
    -e "s|CAMINHO|$APP_DIR|g" \
    deploy/fechadura-digital.service | \
    sudo tee /etc/systemd/system/fechadura-digital.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now fechadura-digital.service
sudo systemctl status fechadura-digital.service
journalctl -u fechadura-digital.service -f
~~~

O serviço começa em LOCK_RELAY_MODE=mock. Somente após os testes, altere as variáveis de ambiente no serviço para o modo GPIO e configure o acesso ao dispositivo para o usuário. Depois execute:

~~~bash
sudo systemctl daemon-reload
sudo systemctl restart fechadura-digital.service
~~~

O serviço não abre porta na rede. Para manutenção a partir de outro computador, use apenas túnel SSH autenticado (não encaminhe a porta para a internet):

~~~bash
ssh -L 8765:127.0.0.1:8765 usuario@IP_DO_ORANGE_PI
~~~

Depois abra http://127.0.0.1:8765 no computador de manutenção.

## Dados, permissões e testes

Os dados ficam por padrão em ~/.local/share/fechadura-digital: banco SQLite, chave biométrica e chave de sessão. A pasta tem modo 0700 e arquivos de chave 0600; dados biométricos são criptografados. **Proteja cópias de backup, acesso ao cartão e senha do sistema operacional.** Sem biometric.key, vetores faciais já cadastrados não podem ser recuperados. Eventos antigos são removidos ao iniciar o aplicativo se tiverem mais de sete dias. A exclusão no painel não remove backups externos.

~~~bash
source .venv/bin/activate
python -m compileall -q lockapp scripts tests
python -m unittest discover -s tests -v
~~~

Os testes não dependem de câmera nem GPIO. Validam senha, CSRF, vetores criptografados, revogação e política de confirmação. Não medem falsos positivos em ambiente real, vivacidade, desempenho do H6, GPIO, ruídos elétricos ou parada de emergência.

## Segurança e implantação real

- **Não há anti-spoofing / liveness:** foto ou vídeo pode enganar um sistema biométrico RGB. Instale um segundo fator (PIN ou RFID) e detecção de apresentação validada antes de usar em portas reais.
- **Falha segura:** a solução elétrica precisa de circuito independente, saída/emergência garantida, sensor de porta, limite físico de acionamento e testes de queda de energia. Não dependa apenas de threads Python para segurança.
- **Ajuste da biometria:** o limiar inicial de similaridade 0,58 não foi calibrado na instalação. Avalie false acceptance/false rejection para pessoas e condições reais.
- **Privacidade/LGPD:** biometria é dado pessoal sensível. Formalize base legal, finalidade, ciência/consentimento quando aplicável, retenção, acesso e exclusão. Não cadastre terceiros sem autorização.
- **Manutenção:** atualize bibliotecas e sistema, use fonte estabilizada/UPS, faça backup criptografado e teste câmera/relé após atualizações.

Referências: [Manual Orange Pi 3 LTS](https://orangepi.vn/wp-content/uploads/2022/07/OrangePi_3_LTS_H6_User-Manul_v2.1.pdf), [OpenCV DNN Face](https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html), [OpenCV Zoo](https://github.com/opencv/opencv_zoo), [libgpiod](https://libgpiod.readthedocs.io/).
