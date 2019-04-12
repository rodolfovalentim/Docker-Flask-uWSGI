from flask import Flask 
import requests 

app = Flask(__name__)

@app.route('/<path:path>')
def proxy(path):
    return requests.get("{}:{}".format(path, "9999"))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=9999)