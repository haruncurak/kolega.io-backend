from flask import Flask, request, jsonify
import firebase_admin
import hashlib
import hmac
from firebase_admin import credentials, firestore
import requests
import json

from google.cloud import secretmanager

from google.cloud import secretmanager_v1
from google.cloud.secretmanager_v1.types import AccessSecretVersionRequest

# Create the Secret Manager client.
client = secretmanager_v1.SecretManagerServiceClient()

# Build the resource name of the secret version.
openai_secret_name = "AIKEY_LOCATION"

# Build the AccessSecretVersionRequest object.
secret_request = AccessSecretVersionRequest(name=openai_secret_name)

# Access the secret version.
openai_response = client.access_secret_version(request=secret_request)

# Extract the payload of the secret version.
OPENAI_API_KEY = openai_response.payload.data.decode('UTF-8')


page_access_token_secret_name = "SECRETNAME_LOCATION"
secret_request = AccessSecretVersionRequest(name=page_access_token_secret_name)
page_access_token_response = client.access_secret_version(request=secret_request)
PAGE_ACCESS_TOKEN = page_access_token_response.payload.data.decode('UTF-8')

firebase_cred = ()

cred = credentials.Certificate('CERTIFICATE_LOCATION')
firebase_admin.initialize_app(cred)
db = firestore.client()
    
## Flask Setup
app = Flask(__name__)


@app.route('/')
def hello():
    return "Hello, World!"

@app.route('/webhook', methods=['GET'])
def verify():
    if request.args.get('hub.verify_token') == 'VERIFY_TOKEN':
        return request.args.get('hub.challenge')
    return 'Error, wrong token', 403


def send_openai_request(messages_content):
    """Send a request to the OpenAI API and get the response."""
    requestBody = {
        "model": "KOLEGA_FINETUNED",
        "messages": messages_content,
    }

    response = requests.post(
        'https://api.openai.com/v1/chat/completions',
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {OPENAI_API_KEY}'
        },
        data=json.dumps(requestBody)
    )
    return response

def extract_assistant_message(response):
    """Extract kolega's message from the OpenAI response."""
    data = response.json()
    return {
        "role": "assistant",
        "content": data["choices"][0]["message"]["content"]
    }

def send_messenger_message(recipient_id, message_text):
    """Send a text message to the user using the Messenger Platform."""
    url = f"https://graph.facebook.com/v13.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    headers = {
        'Content-Type': 'application/json'
    }
    payload = {
        "recipient": {
            "id": recipient_id
        },
        "message": {
            "text": message_text
        }
    }
    response = requests.post(url, headers=headers, json=payload)
    return response

def send_typing_on(sender_id):
    """Send typing_on action to the user."""
    url = f"https://graph.facebook.com/v13.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    headers = {
        'Content-Type': 'application/json'
    }
    payload = {
        "recipient": {
            "id": sender_id
        },
        "sender_action": "typing_on"
    }
    requests.post(url, headers=headers, json=payload)


def send_typing_off(sender_id):
    """Send typing_off action to the user."""
    url = f"https://graph.facebook.com/v13.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    headers = {
        'Content-Type': 'application/json'
    }
    payload = {
        "recipient": {
            "id": sender_id
        },
        "sender_action": "typing_off"
    }
    requests.post(url, headers=headers, json=payload)


@app.route('/webhook', methods=['POST'])
def handle_webhook():
    try:
        data = request.json

        sender_id = data['entry'][0]['messaging'][0]['sender']['id']
        message_text = data['entry'][0]['messaging'][0]['message'].get('text', "")

        # Turn typing indicator on
        send_typing_on(sender_id)

        razgovori_ref = db.collection('razgovori')
        doc_ref = razgovori_ref.document(sender_id)
        doc_snapshot = doc_ref.get()

        base_messages = [
        {
            "role": "system",
            "content": ("Ti si pomoćni učitelj učenicima koji uče matematiku u Bosni i Hercegovini. "
                        "Učenici će ti postavljati pitanja i davati zadatke, a tvoj je zadatak blago im pomoći kako bi oni sami došli do rješenja "
                        "tako što ćeš postavljati pitanja i podpitanja. Ne smiješ otkrivati konkretna rješenja, već samo pružati sugestije koje će im pomoći da razumiju "
                        "kako da dođu do tačnog odgovora. Možeš potvrditi tačnost njihovog rješenja, ali nipošto ne daj direktno rješenje. "
                        "Tvoj cilj je da podučavaš, a ne da rješavaš zadatke umjesto učenika. Budi strpljiv i prijateljski nastrojen. "
                        "Ime ti je Kolega. Odgovaraj isključivo na Bosanskom jeziku.")
        }
    ]

        if doc_snapshot.exists:
            messages_content = doc_snapshot.to_dict().get('messages', base_messages)
        else:
            messages_content = base_messages.copy()

        messages_content.append({"role": "user", "content": message_text})

        # Send a request to OpenAI
        response = send_openai_request(messages_content)

        if response.status_code != 200:
            raise Exception("Error calling OpenAI: " + response.text)

        # Append the assistant's response
        assistant_message = extract_assistant_message(response)
        messages_content.append(assistant_message)

        # Update Firestore
        if doc_snapshot.exists:
            doc_ref.update({'messages': messages_content})
        else:
            doc_ref.set({'messages': messages_content})

        # Create Messenger response from Kolega
        send_typing_off(sender_id)
        messenger_response = send_messenger_message(sender_id, assistant_message["content"])

        # Check the response from Messenger to ensure the message was sent successfully
        if messenger_response.status_code != 200:
            print("Error sending message to Messenger:", messenger_response.text)
            return jsonify({"status": "error", "message": "Failed to send message to Messenger"}), 500

        return jsonify({"status": "success"}), 200

    except Exception as e:
        # Log the exception for debugging purposes
        print(e)
        return jsonify({"status": "error", "message": str(e)}), 500
    


if __name__ == '__main__':
    app.run(port=3000)

