from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/")
async def home():
    return {"message": "Server running 🚀"}

@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy():
    return """
    <html>
        <head>
            <title>Privacy Policy</title>
        </head>
        <body style="font-family: Arial; padding: 20px;">
            <h1>Privacy Policy</h1>

            <p>
                This application collects and processes user data such as email,
                messages, and account information to provide automation and AI services.
            </p>

            <h3>Data Usage</h3>
            <p>
                We use your data only to provide services like email automation,
                WhatsApp messaging, and AI-based responses.
            </p>

            <h3>Data Sharing</h3>
            <p>
                We do not sell or share your personal data with third parties.
            </p>

            <h3>Security</h3>
            <p>
                Your data is stored securely and protected against unauthorized access.
            </p>

            <h3>Contact</h3>
            <p>
                Email: chetansuthar734@gmail.com
            </p>

        </body>
    </html>
    """
