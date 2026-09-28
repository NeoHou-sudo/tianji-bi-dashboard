#!/usr/bin/env bash
# =====================================================================
# 天玑 · 售后看板  ——  Ubuntu / Debian 一键部署脚本
#
# 用法（把整个项目上传到服务器后，在项目目录里执行）：
#   cd ~/tianji-bi && bash deploy/install-ubuntu.sh
#
# 它会做：装 python3 → 建低权用户 → 生成 .env → 装 systemd 服务 → 放行端口 → 启动
# 跑完会打印访问地址。之后编辑 .env 填 AI 密钥 / 飞书凭证即可。
# =====================================================================
set -e

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${BI_PORT:-8899}"

echo "==> 项目目录：$APP_DIR"
echo "==> 服务端口：$PORT"

# 1) 系统依赖（本项目只用 Python 标准库）
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -y
  sudo apt-get install -y python3 curl
else
  echo "（非 apt 系统，跳过依赖安装，请确保有 python3）"
fi

# 2) 低权运行用户
sudo id -u tianji >/dev/null 2>&1 || sudo useradd -r -m -d /home/tianji -s /usr/sbin/nologin tianji

# 3) 生成 .env（若不存在），并适配"IP+端口直连"场景
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/deploy/.env.example" "$APP_DIR/.env"
  # 直连场景：监听所有网卡；HTTP 下 cookie 不能加 Secure
  sed -i "s/^BI_HOST=.*/BI_HOST=0.0.0.0/" "$APP_DIR/.env"
  sed -i "s/^BI_COOKIE_SECURE=.*/BI_COOKIE_SECURE=0/" "$APP_DIR/.env"
  sed -i "s/^BI_PORT=.*/BI_PORT=${PORT}/" "$APP_DIR/.env"
  echo "==> 已生成 .env（记得填 AI_API_KEY / FEISHU_APP_ID / FEISHU_APP_SECRET）"
else
  echo "==> 已存在 .env，保持不变"
fi
chmod 600 "$APP_DIR/.env"
sudo chown -R tianji:tianji "$APP_DIR"

# 4) systemd 服务（把单元文件里的 /opt/tianji-bi 换成本目录）
sudo sed "s#/opt/tianji-bi#${APP_DIR}#g" "$APP_DIR/deploy/tianji-bi.service" \
  | sudo tee /etc/systemd/system/tianji-bi.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now tianji-bi

# 5) 放行端口（有 ufw 就开；云厂商的安全组要另外在控制台放行）
if command -v ufw >/dev/null 2>&1; then
  sudo ufw allow "${PORT}/tcp" || true
fi
# Oracle/部分镜像默认 iptables 会挡，尝试放行
sudo iptables -I INPUT -p tcp --dport "${PORT}" -j ACCEPT 2>/dev/null || true

# 6) 自检 + 打印地址
sleep 2
if curl -s -o /dev/null --max-time 5 "http://127.0.0.1:${PORT}/tianji.html"; then
  echo "==> 本机自检：OK"
else
  echo "==> 本机自检失败，请执行：journalctl -u tianji-bi -n 50 --no-pager"
fi

PUBIP="$(curl -s --max-time 5 ifconfig.me 2>/dev/null || echo 服务器公网IP)"
echo ""
echo "=============================================================="
echo " ✅ 部署完成，访问地址： http://${PUBIP}:${PORT}/tianji.html"
echo ""
echo " 别忘了："
echo "  1) 在云控制台「安全组/防火墙」放行 TCP ${PORT}"
echo "  2) 编辑 .env 填 AI_API_KEY、FEISHU_APP_ID、FEISHU_APP_SECRET"
echo "  3) 改完重启： sudo systemctl restart tianji-bi"
echo "  4) 看日志：   journalctl -u tianji-bi -f"
echo "=============================================================="
