from fastapi import FastAPI

app = FastAPI(title="mras-composer")


@app.get("/health")
def health():
    return {"status": "ok"}
