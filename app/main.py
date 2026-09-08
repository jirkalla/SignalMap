from fastapi import FastAPI

app = FastAPI(title="SignalMap")

@app.get("/health")
def health():
    return {"status": "ok"}