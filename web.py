from flask import Flask
import threading
import os

app = Flask(__name__)

@app.route("/")
def home():
    return "Kalemo Bot is alive!", 200

def run_web():
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )

threading.Thread(target=run_web).start()