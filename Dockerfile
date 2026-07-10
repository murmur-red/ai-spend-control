# ai-spend-control — 💸 buy-side SaaS+AI spend control (Streamlit on Fly.io).
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python inject_og.py   # inject Open Graph tags into Streamlit's static shell
ENV PORT=8501
EXPOSE 8501
CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT} --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false"]
