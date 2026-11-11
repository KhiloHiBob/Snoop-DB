def main() -> None:
    import uvicorn

    uvicorn.run("snoop_db.api:create_app", factory=True, host="127.0.0.1", port=8000)
