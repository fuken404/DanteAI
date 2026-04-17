#!/bin/bash
set -e

echo "=== Instalando Dante en Oracle Cloud ==="

# Dependencias del sistema
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv git

# Clonar repo
cd /home/ubuntu
git clone https://github.com/fuken404/DanteAI.git dante-bot
cd dante-bot

# Entorno virtual e instalar dependencias
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Crear archivo .env
if [ ! -f .env ]; then
    echo "Creando .env..."
    read -p "TELEGRAM_TOKEN: " TELEGRAM_TOKEN
    read -p "ANTHROPIC_API_KEY: " ANTHROPIC_API_KEY
    read -p "GROQ_API_KEY: " GROQ_API_KEY

    cat > .env <<EOF
TELEGRAM_TOKEN=$TELEGRAM_TOKEN
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
GROQ_API_KEY=$GROQ_API_KEY
EOF
    echo ".env creado."
fi

# Servicio systemd para que Dante arranque solo al reiniciar
sudo tee /etc/systemd/system/dante.service > /dev/null <<EOF
[Unit]
Description=Dante AI Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/dante-bot
EnvironmentFile=/home/ubuntu/dante-bot/.env
ExecStart=/home/ubuntu/dante-bot/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable dante
sudo systemctl start dante

echo ""
echo "=== Dante instalado y corriendo ==="
echo "Ver logs: sudo journalctl -u dante -f"
