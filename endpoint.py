from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/mpesa-callback', methods=['POST'])
def mpesa_callback():
    # Log the entire callback payload
    callback_data = request.json
    print("M-Pesa Callback Received:")
    print(json.dumps(callback_data, indent=2))
    
    # Basic validation and response
    if callback_data:
        return jsonify({"status": "success"}), 200
    else:
        return jsonify({"status": "error"}), 400

if __name__ == '__main__':
    app.run(port=5000, debug=True)