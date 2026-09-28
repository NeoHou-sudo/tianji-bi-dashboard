# =====================================================================
# 天玑 · 售后看板  ——  Render 部署镜像
# 纯 Python 标准库，无需 pip 安装任何依赖，镜像很小、构建很快
# =====================================================================
FROM python:3.12-slim

# 时区（快照 / 日志时间按国内）
ENV TZ=Asia/Shanghai
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
 && echo "Asia/Shanghai" > /etc/timezone

WORKDIR /app
COPY . .

# Render 会注入 PORT 环境变量，bi_server.py 会自动读取；
# 云上必须监听 0.0.0.0；HTTPS 站点下 cookie 加 Secure
ENV BI_HOST=0.0.0.0 \
    BI_COOKIE_SECURE=1 \
    PYTHONUNBUFFERED=1

# 说明：Render 用 $PORT 决定监听端口，这里不用写 EXPOSE 具体值
CMD ["python3", "bi_server.py"]
