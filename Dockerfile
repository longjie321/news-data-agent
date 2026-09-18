# ============================================================
# 【这个文件干什么】
#   把项目打成 Docker 镜像。构建：
#       docker build -t news_agent .
#   运行：
#       docker run -p 8020:8020 --env-file .env news_agent
#
#   （更方便的是用 docker-compose，见 docker-compose.yml）
# ============================================================

# 用官方精简版 Python 镜像（slim 比完整版小很多）
FROM python:3.13-slim

# 容器默认是 UTC 时间，日志里的时间会差 8 小时，所以设成东八区
ENV TZ=Asia/Shanghai

# 让 Python 的输出不缓冲 —— 否则 docker logs 里看不到实时日志
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# ⚠️ 顺序很重要：先复制依赖清单、再装依赖，最后才复制代码。
#   因为 Docker 是分层缓存的：只要 requirements.txt 没变，
#   以后改业务代码重新构建时，装依赖这一层直接用缓存，构建快很多。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再复制项目代码（.venv / .env 这些会被 .dockerignore 排除掉）
COPY . .

EXPOSE 8020

# ⚠️ 必须监听 0.0.0.0，不能是 127.0.0.1。
#   容器里的 127.0.0.1 只指"容器自己"，外面是连不进来的。
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8020"]
