#!/bin/bash
set -e

echo "======================================"
echo "    NanoStreamer 1-Click Installer"
echo "======================================"

# 1. Update and install dependencies
echo "[1/6] Updating system and installing dependencies..."
apt update && apt upgrade -y
apt install -y curl wget git python3 python3-pip python3-venv ffmpeg

# 2. Setup working directory
INSTALL_DIR="/opt/nanostreamer"
echo "[2/6] Setting up installation directory at $INSTALL_DIR..."
mkdir -p $INSTALL_DIR
cp -r ./* $INSTALL_DIR/ || true
cd $INSTALL_DIR

# 3. Setup Python Virtual Environment
echo "[3/6] Setting up Python environment..."
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Install MediaMTX
echo "[4/6] Installing MediaMTX..."
wget -q https://github.com/bluenviron/mediamtx/releases/download/v1.6.0/mediamtx_v1.6.0_linux_amd64.tar.gz -O mediamtx.tar.gz
tar -xzf mediamtx.tar.gz mediamtx
rm mediamtx.tar.gz
chmod +x mediamtx

# 5. Create Systemd Services
echo "[5/6] Creating Systemd Services..."

# MediaMTX Service
cat <<EOF > /etc/systemd/system/mediamtx.service
[Unit]
Description=MediaMTX Server
After=network.target

[Service]
ExecStart=$INSTALL_DIR/mediamtx $INSTALL_DIR/mediamtx.yml
WorkingDirectory=$INSTALL_DIR
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

# NanoStreamer Service
cat <<EOF > /etc/systemd/system/nanostreamer.service
[Unit]
Description=NanoStreamer Dashboard
After=network.target mediamtx.service

[Service]
ExecStart=$INSTALL_DIR/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
WorkingDirectory=$INSTALL_DIR
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

# 6. Enable and Start Services
echo "[6/6] Starting Services..."
systemctl daemon-reload
systemctl enable mediamtx
systemctl enable nanostreamer
systemctl restart mediamtx
systemctl restart nanostreamer

echo "======================================"
echo " Instalasi Selesai!"
echo " Akses Dashboard di: http://<IP_SERVER>:8000"
echo " Username default: muis24"
echo " Password default: master123"
echo "======================================"
