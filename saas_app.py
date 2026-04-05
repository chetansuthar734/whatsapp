
# 🆘 for start mongdb run in ternminal         : mongod 


# !pip install python-jose
from fastapi import FastAPI, HTTPException,Depends,  Form,Request,Header,UploadFile,File,Query,WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse,Response,PlainTextResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import jwt, JWTError
from passlib.context import CryptContext
from pymongo import MongoClient
from datetime import datetime, timedelta
from fastapi.middleware.cors import CORSMiddleware
# from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
import os
from fastapi.responses import HTMLResponse

import gridfs
from faster_whisper import WhisperModel
import numpy as np
import json
from bson import ObjectId
from uuid import uuid4
import fitz  # PyMuPDF
import requests




from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger


scheduler = AsyncIOScheduler()


app = FastAPI()


@app.on_event('startup')
async def startup():
    scheduler.start()
    print('❤️ server start') 



@app.on_event('shutdown')
async def shutdown():
    scheduler.shutdown()


react_build_dir = os.path.join(os.path.dirname(__file__), "build")



app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # you can restrict to specific domains later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- MongoDB Setup ----------------
client = MongoClient("mongodb://localhost:27017")
db = client["user_auth_db"]
db2 = client["video_db"]
users_collection = db["users"]
blacklist = db["token_blacklist"]

fs = gridfs.GridFS(db2)



BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")

def save_credentials_to_gridfs():
    with open(CREDENTIALS_FILE, "rb") as f:
        data = f.read()

    credentials_file_id = fs.put(
        data,
        filename="credentials.json",
        contentType="application/json",
        metadata={
            "type": "gmail_oauth_client",
            "owner": "server"
        }
    )

    print("✅ credentials.json saved to GridFS")
    print("🆔 credentials_file_id:", credentials_file_id)

    return credentials_file_id

credentials_file_id =save_credentials_to_gridfs()





# ---------------- App Setup ----------------

SECRET_KEY = "mysecretkey"  # change to a secure one
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# ---------------- Password Hashing ----------------
pwd_context = CryptContext(schemes=["bcrypt"],deprecated="auto")



def hash_password(password: str):
    print("password",password)
    # return password
    return pwd_context.hash(password)

def verify_password(plain, hashed):
    print('plain',plain)
    # return plain==hashed
    return pwd_context.verify(plain, hashed)

# ---------------- JWT Token Helpers ----------------

# token = create_access_token({"sub": user["username"]}) use in @app.post('/login')
def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)



# oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)

def get_current_user(token: str = Depends(oauth2_scheme)):
    if not token:
        raise HTTPException(status_code=401, detail="Token missing")
        #  1. Check blacklist
    if blacklist.find_one({"token": token}):
        raise HTTPException(status_code=401, detail="Token is revoked (logout)")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = users_collection.find_one({"username": username})
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")




@app.get("/")
def serve_react():
    return FileResponse(f"{react_build_dir}/index.html")


@app.post("/register")
# def register(username: str, password: str):
def register(username: str = Form(...), password: str = Form(...)):
    if users_collection.find_one({"username": username}):
        raise HTTPException(status_code=400, detail="Username already registered")

    hashed = hash_password(password)
    users_collection.insert_one({"username": username, "password": hashed})
    return {"message": "User registered successfully"}



@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required")

    user = users_collection.find_one({"username": username})
    if not user or not verify_password(password, user["password"]):
        raise HTTPException(status_code=400, detail="Invalid username or password")

    token = create_access_token({"sub": username})
    return {"access_token": token, "token_type": "bearer"}

# app.mount("/", StaticFiles(directory=react_build_dir, html=True), name="react")


# Logout (invalidate JWT)
# ----------------------------------------
@app.get("/logout")
async def logout(token: str = Depends(oauth2_scheme)):
    # store token in blacklist
    
    blacklist.insert_one({"login_token": token})
    return {"message": "Logged out successfully. Token is now invalid."}
  







@app.get("/profile")
def read_users_me(current_user: dict = Depends(get_current_user)): # get_current_user func take token from and return user
    current_user["_id"] = str(current_user["_id"])
    return current_user

class SubscribeBody(BaseModel):
    plan: str
    cycle: str

import json 

@app.post("/subscribe")
def read_users_me(body: SubscribeBody, current_user: dict = Depends(get_current_user)):
    print("User:", current_user)
    print("User selected:", body.plan, body.cycle)

    # Update user plan in DB
    users_collection.update_one(
        {"username": current_user["username"]},    # filter
        {"$set": {"plan": f"{body.plan}_{body.cycle}"}}  # value
    )

    current_user["_id"] = str(current_user["_id"])
    current_user["plan"] = f"{body.plan}_{body.cycle}"
    return current_user



import hmac
import hashlib
import json


@app.post("/upload")
async def upload(file:UploadFile=File(...),current_user = Depends(get_current_user)):
       # Validate file type
    VALID_TYPES = ["video/mp4", "image/jpg","image/jpeg","image/png","image/webp","image/gif" , "audio/x-wav","audio/mpeg",'application/pdf']
    if file.content_type not in VALID_TYPES:
        # print(file.content_type)
        if file.content_type=="application/octet-stream":
            raise HTTPException(status_code=400, detail="file is empty!")

        raise HTTPException(status_code=400, detail="Unsupported file format")
    

      # Save in GridFS
    file_id = fs.put(file.file, filename=file.filename, content_type=file.content_type)

    users_collection.update_one(
        {"username": current_user["username"]},
        {"$push": {"files": str(file_id)}}
    )

    # file_bytes = file.read() 
    # print('file_id',file.size)
    # print('file_name',file.filename)
    # print('file_type',file.content_type)

    return {'file_id':str(file_id)} 


#client 
# <img src="http://localhost:8000/files/<FILE_ID>?token=<JWT_TOKEN>" />



@app.get("/files/{file_id}")
# async def get_video(file_id: str, current_user=Depends(get_current_user)):
async def get_file(file_id: str,token:str=Query(...)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"verify_exp": False})
        username = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        user = users_collection.find_one({"username": username})
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        # return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Check ownership

    user = users_collection.find_one(
        {"username":username, "files": file_id}
    )
    if not user:
        raise HTTPException(status_code=403, detail="Not allowed file_id not own by user")

    # Fetch file
    try:
        grid_out = fs.get(ObjectId(file_id))
    except:
        raise HTTPException(status_code=400, detail="Invalid file_id")
    

     #for direct load by doc url 
    return Response(
        content=grid_out.read(), 
        media_type=grid_out.content_type
    )






import os
import base64
from email.mime.text import MIMEText

from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify"
    ]

REDIRECT_URI = "http://localhost:9999/auth/gmail/callback"


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json") #for server only , for get callback / webhook  

# TOKEN_FILE = os.path.join(BASE_DIR, "token.json") # for per user access email 




@app.get("/email-auth")
async def email_auth(current_user: dict = Depends(get_current_user)):

    # if user id not contain token than redirect to gmail promission
    credentials = fs.get(ObjectId(credentials_file_id))
    cred_bytes = credentials.read()
    # bytes to json 
    cred_dict = json.loads(cred_bytes.decode("utf-8"))

    REDIRECT_URI=f"http://localhost:9999/auth/gmail/{current_user['username']}"
    flow = Flow.from_client_config(
        cred_dict,          # ✅ dict is allowed here
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
        )

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent"
    )

    return {
        # "success": True,
        # "credentials_file_id": str(cred_id),
        # "token_file_id": str(token_id),
        "auth_url":auth_url
    }



@app.get("/auth/gmail/{username}")
def gmail_callback(request: Request,username:str):

    #use creds file id to get client secrets

    code = request.query_params.get("code")
    
    grid_file = fs.get(ObjectId(credentials_file_id))
    cred_bytes = grid_file.read()

    # 2️⃣ bytes → dict
    cred_dict = json.loads(cred_bytes.decode("utf-8"))

    REDIRECT_URI=f"http://localhost:9999/auth/gmail/{username}"
    
    flow = Flow.from_client_config(
        cred_dict,         
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
        )

    flow.fetch_token(code=code)
    token = flow.credentials

    print('token',token)
    print('token to json ',token.to_json())
    
    #save token to gridfs and return token_id add to user in db

    users_collection.update_one(
        {"username": username},
        {"$set": {"gmail_token": str(token.to_json())}}
    )
    print('✅ ✅ redirect to email page')
    return RedirectResponse(url="http://localhost:3000/email_page",status_code=302)    



#get gmail service verified

from google.auth.transport.requests import Request as GoogleRequest
import json


def get_gmail_service(token_dict, cred_dict):

    # Load token
    creds = Credentials.from_authorized_user_info(token_dict, SCOPES)
    print("✅ token loaded")

    # Refresh if expired
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        print("🔄 token refreshed")

    # If no valid creds → OAuth
    if not creds or not creds.valid:
        print("🔐 Starting OAuth flow...")


        flow = InstalledAppFlow.from_client_config(
            cred_dict,
            SCOPES
        )

        creds = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            open_browser=True
        )

        print("✅ new token created")

        # ✅ IMPORTANT: Save token here
        token_json = creds.to_json()
        # store token_json in DB / GridFS

    return build("gmail", "v1", credentials=creds)


#email app for read email and write email
@app.get('/email_token_validation')
async def emailapp(current_user: dict = Depends(get_current_user)):
    try:
        gmail_token_string = current_user.get('gmail_token')
        print(gmail_token_string)

        # ✅ Convert string → dict (important)
        token_dict = json.loads(gmail_token_string)
        grid_file = fs.get(ObjectId(credentials_file_id))
        cred_bytes = grid_file.read()
        # 2️⃣ bytes → dict
        cred_dict = json.loads(cred_bytes.decode("utf-8"))
        service = get_gmail_service(token_dict,cred_dict)

        # ✅ Safe copy for ObjectId conversion
        user_data = current_user.copy()
        user_data['_id'] = str(user_data['_id'])

        return 
        {
            "gmail_token": gmail_token_string
        }

    except Exception as e:
        print(e)
        raise HTTPException(status_code=401, detail="Invalid gmail token")

# @app.get('/gmail_token_verify')
# async def gmail_token_verify(current_user: dict = Depends(get_current_user)):
#     try:
#         token_bytes = current_user.get("gmail_token")

#         if not token_bytes:
#             raise HTTPException(status_code=400, detail="No Gmail token found")

#         # ✅ Load token
#         token_dict = json.loads(token_bytes.decode("utf-8"))
#         creds = Credentials.from_authorized_user_info(token_dict, SCOPES)

#         # ✅ Check validity
#         if not creds.valid:
#             if creds.expired and creds.refresh_token:
#                 creds.refresh(GoogleRequest())
#             else:
#                 raise HTTPException(status_code=401, detail="Token expired or invalid")

#         return {
#             "status": "valid",
#             "email": current_user.get("username")
#         }

#     except Exception as e:
#         print("Error:", e)
#         raise HTTPException(status_code=401, detail="Invalid token")    


#read gamil route using gmail_token , 
#send gamil  route using gmail_token , 
#read gmail in interval and ai agent generate response and send gmail




class Appointment(BaseModel):
    task_id: str
    date: str
    start_time: str
    end_time: str


@app.post("/add_appointment")
async def add_appointment(
    payload: Appointment,
    current_user: dict = Depends(get_current_user)
    ):
    users_collection.update_one(
        {"username": current_user["username"]},
        {"$push": {"appointment": payload.dict()}}
    )

    return {"appointment": payload}     



@app.get('/appointments')
async def appointments(current_user: dict = Depends(get_current_user)):
    try:
        # print(current_user)

        appointments = current_user['appointment']
        print(appointments)

        return appointments
    except:
        print('error')
        return {"error":"error"}
            


class DeleteAppointment(BaseModel):
    task_id: str



@app.delete("/delete_appointment")
async def delete_appointment(
    payload: DeleteAppointment,
    current_user: dict = Depends(get_current_user)
    ):
    print("payload:", payload)
    users_collection.update_one(
            {"username": current_user["username"]},
            {"$pull": {
                "appointment": {
                    "task_id": payload.task_id,
                }
                }
            }
            )

    return {"status": "deleted", "task_id": payload.task_id}















# async def  user_task(user_id:str,message:str):
#       websocket is connection check
#     if not Conn_Manager.is_connected(user_id):
#         print(f"{user_id} is not connected skip task run or paused task")
#         return 
    
#     print(f"running task for {user_id}")
#     await Conn_Manager.send(user_id,message)

async def user_task(user_id:str,message:str):
        print(f"{user_id} task run or paused task")
        return 




@app.post('/interval')
async def schedule(req:Request,current_user: dict = Depends(get_current_user)):
    try:
        data = await req.json()
        print(data)
        client_name = current_user.get('username')
        print('client id ',client_name)

        trigger = IntervalTrigger(seconds=data['sec'],minutes=data['min'],hours=data['hr'])
        job_id = f"{client_name}_{uuid4()}"
        print(job_id)
        scheduler.add_job(
            user_task,
            trigger=trigger,
            args=[client_name,data['message']],
            id=job_id
            )
        return {"interval schedule task is done":data}
    except:
        print('error')
        return {"error":"error"}
            


@app.post('/cron_schedule')
async def schedule(req:Request,current_user: dict = Depends(get_current_user)):
    try:
        data =await req.json()   
        dt_str = data.get("datetime")
        run_date = datetime.fromisoformat(dt_str)
        client_name = current_user.get('username')
        job_id = f"{client_name}_{uuid4()}"
        scheduler.add_job(
            user_task,
            args=[current_user['username'],data.get('message',"")],
            trigger="date",
            run_date=run_date,
            id =job_id)
        return {"cron schedule task is done":data}

    except:
        print('error')
        return {"error":"error"}
            


@app.get("/jobs")
async def list_jobs(current_user: dict = Depends(get_current_user)):
    jobs = []
    client_id = current_user.get('username')
    for job in scheduler.get_jobs():
        if job.id.startswith(client_id):
            print(job)
            jobs.append({
                "id": job.id,
                "next_run_time": str(job.next_run_time),
                "trigger": str(job.trigger),
                })
    
    return jobs



@app.delete("/jobs/{job_id}")
async def remove_job(job_id:str):
    scheduler.remove_job(job_id)
    print('task is delete:',job_id)
    return {"message": "job removed", "job_id": job_id}





@app.post("/userData")
async def email_userData(
    text: str = Form(...),
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
    ):

    return {
        "user name": current_user["username"],
        "text": text,
        "upload file type": file.content_type
    }


@app.post("/userjson")
async def upload_file(
    name: str = Form(...),
    file: UploadFile = File(...)
    ):
    c =await file.read()
    return {
        "name": name,
        "filename": file.filename,
        "file size": len(c)
    }



#route for upload doc,split text chunk and embedding, for vector indexing.


# vector indexing  and search query
import chromadb
# from sentence_transformers import SentenceTransformer
# model = SentenceTransformer('all-MiniLM-L6-v2')
from chromadb import PersistentClient
from langchain_google_genai import GoogleGenerativeAIEmbeddings
import os
os.environ["GOOGLE_API_KEY"] = "AIzaSyARUAeAGOLdr1yz7Q1aqJ9hQieP8dxcKrY" 
embedding = GoogleGenerativeAIEmbeddings(model="gemini-embedding-001")

client = PersistentClient(path="./chroma_db")
# client = chromadb.Client(settings=chromadb.config.Settings(persist_directory="./chroma_db"))
collection = client.get_or_create_collection(name="documents")

from langchain.text_splitter import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)


import uuid

@app.post("/uploadpdf")
async def index(
    file: UploadFile = File(...),
    # current_user: dict = Depends(get_current_user)
    ):
    # ✅ Validate file type
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF allowed")
    # ✅ Read file
    content = await file.read()
    pdf = fitz.open(stream=content, filetype="pdf")

    text = ""
    for page in pdf:
        t = page.get_text()
        if t:
            text += t

    chunks = splitter.split_text(text)
    embeddings = embedding.embed_documents(chunks)
    ids = [str(uuid.uuid4()) for _ in chunks]

    collection.add(
        ids=ids,
        documents=chunks,
        embeddings=embeddings  # ✅ manually pass
        )
    
    return {
        "message": "File indexed successfully",
        "id": ids
        }

@app.get('/search/{query}')
async def search(query:str):
    query_embedding = embedding.embed_query(query)
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=3
        )
    return {
        "query": query,
        "results": results["documents"]
        }



#route for similar search vector.    





















# whatsapp  bot dev 


VERIFY_TOKEN = "chetan55"   # must match Meta dashboard

@app.get("/webhook")
async def verify(request: Request):
    params = request.query_params

    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge)   # OR return challenge (string also works)

    return {"error": "Verification failed"}



@app.post("/webhook")
async def receive_message(request: Request):
    data = await request.json()
    # print("Incoming:", data)

    try:
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
        phone = msg["from"]
        text = msg["text"]["body"]

        print("From:", phone)
        print("Message:", text)

    except:
        pass

    return {"status": "ok"}




APP_ID = "1202498515099329"
APP_SECRET = "c1fcf7737fbbbc8de7dbe1c508314f38"
# REDIRECT_URI = "https://9c1b-2409-4090-a057-9003-991a-f703-c710-ef86.ngrok-free.app/whatsapp_callback" #ngrok url
REDIRECT_URI = "https://9c1b-2409-4090-a057-9003-991a-f703-c710-ef86.ngrok-free.app/whatsapp_callback"
 #ngrok url
# ACCESS_TOKEN="EAAMaN8gT92MBRBRvZAmRDZCPiFQEQi9fHoeZBTENiTdJcZBGPxm2c3Eq4MWhIeUT0uf36fPw6NMdpnYuJm2ZBeJg4wCpMhH9kj0uGMTTKJoO3AvBK1vtEgvvFzBT1vLVXM0j2YBVKmGLFYF84UQgTAS6p02rfcZAZChNGJvaixZCStAHsPNREMTuIaZAgFgAYVDH6iB8WEAkROMDIJycZCzqyENJv8ZB1wotPQeaqLkh6dWktb7rwUt6BNqxa59YZAa5RbWBuzQ1Xc0yhldhUUAe3sZAHarOPXwZDZD"
# PHONE_NUMBER_ID="1035406206326982"

# @app.get('/whatsapp_login')
# def login():
#     oauth_url = (f"https://www.facebook.com/v18.0/dialog/oauth"
#                  f"?client_id={APP_ID}"
#                  f"&redirect_uri={REDIRECT_URI}"
#                  f"scope=whatsapp_business_management,whatsapp_business_messaging"
#                  f"response_type": "code"
#                  )
#     return RedirectResponse(oauth_url)


from urllib.parse import urlencode


@app.get('/whatsapp_login')
def login( current_user: dict = Depends(get_current_user)):
    user_id = current_user['username']
    params = {
        "client_id": APP_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "whatsapp_business_management,whatsapp_business_messaging",
        "response_type": "code",
         "state": user_id   # ✅ store user पहचान
    }

    oauth_url = f"https://www.facebook.com/v18.0/dialog/oauth?{urlencode(params)}"
    # return RedirectResponse(oauth_url)
    return {
        # "success": True,
        # "credentials_file_id": str(cred_id),
        # "token_file_id": str(token_id),
        "auth_url":oauth_url
    }


@app.get("/whatsapp_callback")
def callback(code:str,state:str):   #state 
    username= state
    print("❤️ username:", username)
    token_url = 'https://graph.facebook.com/v18.0/oauth/access_token'
    params={ 'client_id':APP_ID,
             'client_secret':APP_SECRET,
             'redirect_uri':REDIRECT_URI ,
             'code':code
            }
    response = requests.get(token_url,params=params)
    data = response.json()
    access_token=data.get('access_token')

    # save whatasapp access token in db crossponds user_id
     #save token to gridfs and return token_id add to user in db

    users_collection.update_one(
        {"username": username},
        {"$set": {"whatsapp_access_token": str(access_token)}}
    )


    # res1 = requests.get( "https://graph.facebook.com/v18.0/me/businesses",params={"access_token": access_token})

    # business_data = res1.json()
    # print("BUSINESSES:", business_data)
    # business_id = business_data["data"][0]["id"]

    # res2 = requests.get( f"https://graph.facebook.com/v18.0/{business_id}/owned_whatsapp_business_accounts",params={"access_token": access_token})

    # waba_data = res2.json()
    # print("WABA:", waba_data)
    # waba_id = waba_data["data"][0]["id"]

    # res3 = requests.get(f"https://graph.facebook.com/v18.0/{waba_id}/phone_numbers",params={"access_token": access_token})

    # phone_data = res3.json()
    # print("PHONE:", phone_data)
    # phone_number_id = phone_data["data"][0]["id"]



    return RedirectResponse(url="http://localhost:3000/whatsapp_page",status_code=302)    

    # return { "access_token":data.get('access_token'),
    #          "token_type":data.get('token_type') }
            





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






# ACCESS_TOKEN="EAAMaN8gT92MBRBRvZAmRDZCPiFQEQi9fHoeZBTENiTdJcZBGPxm2c3Eq4MWhIeUT0uf36fPw6NMdpnYuJm2ZBeJg4wCpMhH9kj0uGMTTKJoO3AvBK1vtEgvvFzBT1vLVXM0j2YBVKmGLFYF84UQgTAS6p02rfcZAZChNGJvaixZCStAHsPNREMTuIaZAgFgAYVDH6iB8WEAkROMDIJycZCzqyENJv8ZB1wotPQeaqLkh6dWktb7rwUt6BNqxa59YZAa5RbWBuzQ1Xc0yhldhUUAe3sZAHarOPXwZDZD"
ACCESS_TOKEN= "EAARFqoDV3sEBRNSOxzW6yDCcchxLLrgbZAIYZAJQjghHUPcx37slymZAvC8Q47kzMcqK8dRcXhMmkjKsQ1ajbpjiZA3G5HGzjOmKd2DkzyuLXIEcZBXWi7pZAFrRZCmEhRoEkoD4LOFQqewrWrvlcikAZCVyiefrSBpNVoDgWct6uZC4H7z3cLbopXHf6rJZBWYTMrdPu3hP6ZClh99k15jkxNb1zQUnliHh4hEx4HJnTCLNxrrxhSOKleR30FC6cehCfp34N72BOwH2Iidb1xMgVZBVOAb6f03CD3PFwLx8"
PHONE_NUMBER_ID="1117917481394388"

@app.get("/send_whatsapp")
def send_whatsapp_message(to: str, message: str):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "to": to,  # format: 91XXXXXXXXXX
        "type": "text",
        "text": {
            "body": message
        }
    }

    response = requests.post(url,headers=headers,json=data)

    return {
        "status": response.status_code,
        "response": response.json()
    }


def validate_token(input_token, app_token):
    url = "https://graph.facebook.com/debug_token"

    params = {
        "input_token": input_token,
        "access_token": app_token   # APP_ID|APP_SECRET
    }

    res = requests.get(url, params=params)
    return res.json()


# 🔥 Example
APP_TOKEN = f"{APP_ID}|{APP_SECRET}"

@app.get('/whatsapp_token_validation')
async def emailapp(current_user: dict = Depends(get_current_user)):
    try:
        whatsapp_access_token = current_user.get('whatsapp_access_token')
        print("whatsapp_access_token",whatsapp_access_token)

        # ✅ Safe copy for ObjectId conversion
        user_data = current_user.copy()
        user_data['_id'] = str(user_data['_id'])
        
        result = validate_token(whatsapp_access_token, APP_TOKEN)
        
        if result["data"]["is_valid"]:
            print("✅ Token valid")
            return{ "status":200, "whatsapp_access_token": whatsapp_access_token}
        else:
            print("❌ Token invalid")
            raise HTTPException(status_code=401, detail="Invalid gmail token")
 


        return{"whatsapp_access_token": whatsapp_access_token}


    except Exception as e:
        print(e)
        raise HTTPException(status_code=401, detail="Invalid gmail token")

















#voice agent app
# from fastapi import FastAPI, WebSocket
# from fastapi.middleware.cors import CORSMiddleware
# from silero_vad import load_silero_vad, read_audio, get_speech_timestamps
# from pysilero_vad import SileroVoiceActivityDetector
# import numpy as np
# vad = SileroVoiceActivityDetector()
# import torch
# import time

# # print("print", vad.chunk_samples)
# from faster_whisper import WhisperModel

# import os
# os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# # model_stt = WhisperModel("small", device="cpu", compute_type="float32")
# model_stt = WhisperModel("tiny", device="cpu", compute_type="int8")

# app = FastAPI()
# import wave
# from asyncio import to_thread
# import asyncio 
# from langchain_google_genai import ChatGoogleGenerativeAI
# from dataclasses import dataclass
# from uuid import uuid4
# import noisereduce as nr
# # llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash" ,google_api_key="AIzaSyByWx8Q_jCOtYcB92ZCk4AvYiZKnlTZ3-c")

# # print(llm.invoke('hi'))

#                           #TRANSCRIPTION
# async def STT(pcm_float32):
#                             # segments, info = model_stt.transcribe(pcm_float32, language="en")
#                             # segments, info = await asyncio.to_thread(model_stt.transcribe, pcm_float32, "en")
#     try:
#         segments, info = await asyncio.to_thread(model_stt.transcribe, pcm_float32, "en")
#         full_text = ""
#         for seg in segments:      
#             full_text += seg.text + " "
#         print("TRANSCRIPTION:", full_text.strip())
#     except Exception as e:
#         print("Transcription failed:", e)


  


# def reduce_noise_float32(float32_pcm, sr=16000):
#     # float32 input: [-1, 1]
#     # Use first 0.5 sec as noise profile (optional)
#     noise_profile = float32_pcm[:sr//2]

#     reduced = nr.reduce_noise(
#         y=float32_pcm,
#         y_noise=noise_profile,
#         sr=sr,
#         prop_decrease=1.0
#     )
#     return reduced.astype(np.float32)


# async def pcm16_to_wav(pcm_bytes, filename, sample_rate=16000, channels=1):
#     pcm_samples =np.frombuffer(pcm_bytes, dtype=np.int16)

#     with wave.open(filename, 'wb') as wav_file:
#         wav_file.setnchannels(channels)        # mono
#         wav_file.setsampwidth(2)               # int16 = 2 bytes
#         wav_file.setframerate(sample_rate)
#         wav_file.writeframes(pcm_samples.tobytes())

#     print("WAV saved:", filename)

# async def send_audio(ws:WebSocket,pcm_float32):
#     print("🆘 len",len(pcm_float32))

#     # pcm_float32 = reduce_noise_float32(pcm_float32)

#     # await ws.send_bytes(pcm_float32.tobytes())
#     await ws.send_text("start")
#     samples_per_second = 16000     # 1 second at 16kHz

#     for i in range(0, len(pcm_float32), samples_per_second):
#         chunk = pcm_float32[i:i + samples_per_second]

#         # Send this 1-second PCM chunk
#         await ws.send_bytes(chunk.tobytes())

#         # Wait 1 second before sending next chunk
#         await asyncio.sleep(1)
    
#     await ws.send_text("end")



# def int16_to_float32(bytes_data):
#     pcm_i16 = np.frombuffer(bytes_data, dtype=np.int16)
#     pcm_f32 = pcm_i16.astype(np.float32) / 32768.0
#     return pcm_f32

# @dataclass
# class ClientSession():
#     stt_task:asyncio.Task = None
#     tts_task:asyncio.Task = None

# clients:dict[str,ClientSession] = {}


# # @app.on_event('startup')
# # async def startup():

# model, utils = torch.hub.load(
#     repo_or_dir='snakers4/silero-vad',
#     model='silero_vad',
#     force_reload=False
#     )
# _, _, _, VADIterator, _ = utils
# vad_silero = VADIterator(model)

# # Allow all origins (for testing)
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# @app.websocket("/video")
# async def video_ws(websocket: WebSocket):
#     await websocket.accept()
#     try:
#         while True:
#             # data = await websocket.receive_text()  # receive text from client
#             data = await websocket.receive_bytes()  # receive text from client
#             # data = await websocket.receive_json()  # receive text from client
#             # print("Received from client:", data)
#             # await websocket.send_text(data)  # echo back
#             # await websocket.send_json(data)  # echo back
#             await websocket.send_bytes(data)  # echo back
#     except Exception as e:
#         print("Connection closed:", e)



# # @app.websocket("/audio")
# # async def video_ws(websocket: WebSocket):
# #     await websocket.accept()
# #     try:
# #         while True:
# #             data = await websocket.receive_bytes()  # receive text from client
# #             frame = np.frombuffer(data, dtype=np.int16)
# #             # print(frame)
# #             # silero = vad_silero(frame, 16000)
# #             # print("time stamp start and end:",silero)

# #             assert len(data) == vad.chunk_bytes()
# #             p = vad(data) # take raw bytes, 512 chunk size ,16khz,float32
# #             if p>=.3:
# #                 print('probability speech detect',p)
            
            
# #             # data = await websocket.receive_bytes()  # receive text from client
# #             # data = await websocket.receive_json()  # receive text from client
# #             # print("Received from client:", data)
# #             # await websocket.send_bytes(data)  # echo back
# #             # await websocket.send_json(data)  # echo back
# #             # await websocket.send_bytes(data)  # echo back
# #     except Exception as e:
# #         print("Connection closed:", e)







# SAMPLE_RATE = 16000
# CHUNK_SIZE = vad.chunk_bytes()           # your VAD chunk size (512 bytes)
# SILENCE_SECONDS = 2.0
# CHUNK_DURATION = len(np.frombuffer(b'\x00' * CHUNK_SIZE, dtype=np.float32)) / SAMPLE_RATE

# @app.websocket("/audio")
# async def audio_ws(ws: WebSocket):
#     await ws.accept()


#     id = uuid4()
#     clients[id] = ClientSession()
    

#     audio_buffer = []       # store speech frames
#     in_speech = False
#     silence_start = None

#     print("Client connected")

#     try:
#         while True:
#             data = await ws.receive_bytes()



#             # Run VAD
#             p = vad(data)   # probability
#             print("VAD:", p)

#             if p >= 0.2:   # speech detected
#                 if not in_speech:
#                     print("🎤 Speech started")
#                     in_speech = True
#                     audio_buffer = []
#                     # if clients[id].tts_task and clients[id].tts_task.done():
#                         # audio_buffer=[]
                    
#             # stop speaking IMMEDIATELY when user talks
#             # if clients[id].tts_task:
#                 # clients[id].tts_task.cancel()
#                 # try:
#                     # await clients[id].tts_task
#                 # except:
#                     # pass


#                 audio_buffer.append(data)
#                 silence_start = None

#             else:
#                 # Silence detected
#                 if in_speech:
#                     if silence_start is None:
#                         silence_start = time.time()

#                     # Check if silence reached threshold
#                     if time.time() - silence_start >= SILENCE_SECONDS:
#                         print("🛑 Speech ended — returning to client")

#                         final_audio = b"".join(audio_buffer)


#                         # Convert Int16 → Float32 correctly
#                         pcm_int16 = np.frombuffer(final_audio, dtype=np.int16)
#                         clients[id].stt_task = asyncio.create_task(pcm16_to_wav(final_audio, "output.wav"))

#                         # pcm_float32 = (pcm_int16 / 32768.0).astype(np.float32)
#                         pcm_float32 = pcm_int16.astype(np.float32) / 32768.0 

#                         # await ws.send_bytes(pcm_float32.tobytes())
#                         clients[id].tts_task = asyncio.create_task(send_audio(ws,pcm_float32))

                        

                       
                        
#                         asyncio.create_task(STT(pcm_float32))

                    



        



#                         # Reset
#                         in_speech = False
#                         silence_start = None
#                         audio_buffer = []

#                         # if clients[id].tts_task and clients[id].tts_task.done():
#                             # audio_buffer = []

#     except Exception as e:
#         print("Client disconnected:", e)





# # SAMPLE_RATE = 16000
# # CHUNK_SIZE = vad.chunk_bytes()
# # SILENCE_SECONDS = 2.0
# # CHUNK_DURATION = len(np.frombuffer(b'\x00' * CHUNK_SIZE, dtype=np.float32)) / SAMPLE_RATE

# # @app.websocket("/audio")
# # async def audio_ws(ws: WebSocket):
# #     await ws.accept()
# #     id = uuid4()
# #     clients[id] = ClientSession()
# #     audio_buffer = []
# #     in_speech = False
# #     silence_start = None
# #     print("Client connected")
# #     try:
# #         while True:
# #             data = await ws.receive_bytes()
# #             p = vad(data)
# #             print("VAD:", p)
# #             if p >= 0.2:
# #                 if not in_speech:
# #                     print("🎤 Speech started")
# #                     in_speech = True
# #                     if clients[id].tts_task and clients[id].tts_task.done():
# #                         audio_buffer = []

# #                 if clients[id].tts_task and not clients[id].tts_task.done():
# #                     clients[id].tts_task.cancel()

# #                 audio_buffer.append(data)
# #                 silence_start = None

# #             else:
# #                 if in_speech:
# #                     if silence_start is None:
# #                         silence_start = time.time()

# #                     if time.time() - silence_start >= SILENCE_SECONDS:
# #                         print("🛑 Speech ended — returning to client")

# #                         final_audio = b"".join(audio_buffer)

# #                         # Convert to Int16 → Float32
# #                         pcm_int16 = np.frombuffer(final_audio, dtype=np.int16)
# #                         clients[id].stt_task = asyncio.create_task(
# #                             pcm16_to_wav(final_audio, "output.wav")
# #                         )
# #                         pcm_float32 = pcm_int16.astype(np.float32) / 32768.0

# #                         # Send back audio (optional)
# #                         clients[id].tts_task = asyncio.create_task(
# #                             send_audio(ws, pcm_float32)    )

# #                         # Reset states
# #                         in_speech = False
# #                         silence_start = None

# #                         if clients[id].tts_task and clients[id].tts_task.done():
# #                             audio_buffer = []

# #     except Exception as e:
# #         print("Client disconnected:", e)



# main.py
# FastAPI server that receives audio chunks via websocket, VADs them,
# and offloads transcription to stt_worker.py child process.

import asyncio
import gc
from apscheduler.schedulers.asyncio import AsyncIOScheduler
# from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger



from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from uuid import uuid4
import re
from langchain_core.messages import AIMessage
from fastapi import FastAPI, WebSocket , WebSocketDisconnect,Request

from fastapi.middleware.cors import CORSMiddleware

import numpy as np
from pysilero_vad import SileroVoiceActivityDetector
import torch
import wave
import time

from langchain_google_genai import ChatGoogleGenerativeAI
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash",api_key="AIzaSyARUAeAGOLdr1yz7Q1aqJ9hQieP8dxcKrY") 

# VAD setup
vad = SileroVoiceActivityDetector()

scheduler = AsyncIOScheduler()

# NOTE: The heavy model is NOT loaded here. It's loaded in stt_worker.py.
# If you previously had model_stt = WhisperModel(...) in this file, remove it.


from silero import silero_tts
import sounddevice as sd
import numpy as np
import threading

stop_audio_event = threading.Event()


# Load the v3 English model
tts_model, example_text = silero_tts(language='en', speaker='v3_en')


model, utils = torch.hub.load(
    repo_or_dir='snakers4/silero-vad',
    model='silero_vad',
    force_reload=False
    )
_, _, _, VADIterator, _ = utils
# vad_silero_detect_start_end = VADIterator(model)

app = FastAPI()


@app.on_event('startup')
async def startup():
    scheduler.start()


@app.on_event('shutdown')
async def shutdown():
    scheduler.shutdown()


# Allow CORS for testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Worker executor: single process to keep memory stable
executor = ProcessPoolExecutor(max_workers=1)
import stt_worker  # make sure stt_worker.py is in same directory

# how does voice agent work
# At a high level, every voice agent needs to handle three tasks:
#     1.Listen - capture audio and transcribe it
#.    2.Think - interpret intent, reason, plan
#.    3.Speak - generate audio and stream it back to the user



# Controls / tuning
SAMPLE_RATE = 16000
MAX_SECONDS = 30                 # max seconds to keep before trimming
MAX_SAMPLES = SAMPLE_RATE * MAX_SECONDS
# SILENCE_SECONDS = 0.2
SILENCE_SECONDS = 2.0
VAD_THRESHOLD = 0.3             # vad probability threshold for "speech"
# transcription_semaphore = asyncio.Semaphore(1)  # avoid parallel transcriptions  set 1 or oom out of memory crash prevent for limit parallel users asyncio.semaphore(10)
#that semaphore define global so, mulitple user cannot run parallel
CHUNK_SIZE = vad.chunk_bytes()   # typical 512 bytes


# Helper: convert raw int16 bytes to float32 normalized numpy array
def bytes_to_float32(pcm_bytes: bytes) -> np.ndarray:
    pcm_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
    if pcm_int16.size == 0:
        return np.array([], dtype=np.float32)
    return pcm_int16.astype(np.float32) / 32768.0

# Save WAV (non-blocking via asyncio.create_task)
async def pcm16_to_wav(pcm_bytes: bytes, filename: str, sample_rate: int = SAMPLE_RATE, channels: int = 1):
    try:
        pcm_samples = np.frombuffer(pcm_bytes, dtype=np.int16)
        with wave.open(filename, 'wb') as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_samples.tobytes())
        print("WAV saved:", filename)
    except Exception as e:
        print("pcm16_to_wav error:", e)

# Stream back audio to client in 1-second chunks (fire-and-forget)
async def send_audio(ws: WebSocket, pcm_float32: np.ndarray):
    try:
        print("🆘 len", len(pcm_float32))
        await ws.send_text("start")
        samples_per_second = SAMPLE_RATE
        for i in range(0, len(pcm_float32), samples_per_second):
            chunk = pcm_float32[i:i + samples_per_second]  
            await ws.send_bytes(chunk.tobytes())
            await asyncio.sleep(1)
        await ws.send_text("end")
    except Exception as e:
        print("send_audio error:", e)

# Async wrapper to call stt_worker.run_stt_from_bytes in child process
# “Run this blocking / CPU / IO-heavy function outside the event loop.”

async def stt_via_process(pcm_bytes: bytes) -> str:
    loop = asyncio.get_running_loop()
    # async with transcription_semaphore:
    try:
            # run_in_executor schedules in the ProcessPoolExecutor (child process)
        result = await loop.run_in_executor(executor, stt_worker.run_stt_from_bytes, pcm_bytes)
        return result
    except Exception as e:
        return f"[stt_via_process_error] {repr(e)}"



                    # 1. when websocket is connect
                    # asyncio.create_task(stt_queue_worker(stt_queue)) run in bg.


                    # 2.vad detect audio than,
                    # it first check asyncio.create_task(final_stt_worker(cliend_id)) is run or not, if run than close first.
                    # than set in_speech=True.
                    # set audio_buffer=[]. and
                    # append audio to audio_buffer. and again continue to while loop using "continue" keyword.


                    # 3.when short silence detect
                    #  put audio buffer into stt queue, than 
                    #  set audio_buffer=[]

                    #stt_queue_worker get audio in byte( b"".join(audio_buffer)) transcribe audio and than append transcribe to tex_buffer and
                    # set stt_queue.task_done().

                    # 4.when long silence
                    # if first check final_stt_worker run or not. if run than cancel first
                    # run asyncio.create_task(final_stt_task(client_id)) and 
                    #set silence_start=None
                    #in_speech=False
                    
                    
                    #5.final_Stt_worker task, 

                    # it first run asyncio.create_task(tts_worker(tts_queue)) in bg
                    # await stt_queue.join() ,wait all full text_buffer.
                    # than use sentence (" ".join(text_buffer) ) to run agent .
                    # agent run in stream mode for real time voice send .
                    # stream chunk in sentence detect and than put sentence in tts_worker 
                    # tts_worker send audio_chunk not full audio sentence.
                    # tts model is not stream but ,you can only play/send the generated WAV in chunks






@dataclass
class ClientSession:
    websocket:WebSocket 
    stt_event:asyncio.Event
    send_lock: asyncio.Lock # for one send lock per client
    stt_task: asyncio.Task | None
    final_task: asyncio.Task | None
    stt_queue:asyncio.Queue
    text_buffer:list[str]
    # tts_task: asyncio.Task | None

clients: dict[str,ClientSession] = {}

class ConnectionManager:
    def __init__(self):
        self.active_users:dict[str,ClientSession] = {}

    async def connect(self,user_id:str,clientsession:ClientSession):
        self.active_users[user_id] = clientsession
    
    def disconnect(self,user_id:str):
        self.active_users.pop(user_id,None)

    def is_connected(self,user_id:str)->bool:
        return user_id in self.active_users
    

    async def send(self, user_id: str, message: str):
        client = self.active_users.get(user_id)
        if not client:
            return

        try:
            await client.websocket.send_text(message)
        except Exception as e:
            print(f"Send failed, disconnecting {user_id}: {e}")
            self.disconnect(user_id)

    async def voice_ai_run(self,user_id:str,message:str= ""):
        if message !="":
            # agent run with message and listen
            #final_task = asyncio.create_task(Agent_Run(client_id,message))
            print('agent run')




        

Conn_Manager = ConnectionManager()



# transcibe audio and appent to text buffer list in real time 
async  def stt_queue_worker(client_id):
    while True:
        audio_bytes = await clients[client_id].stt_queue.get()

        # if None put in  stt queue
        if audio_bytes is None:
            clients[client_id].stt_queue.task_done()
            break

        # if list of bytes put in stt queue
        # audio_bytes = b"".join(audio_buffer)
        print(' 🆘✅queue get and transcibe is start')

        # pcm_float32 = bytes_to_float32(audio_bytes) # convert byte into array float32
        # await  clients[client_id].websocket.send_bytes(pcm_float32.tobytes()) 

        #transcribe audio
        # transcribe = await stt_via_process(audio_bytes)
        transcribe  = "hello?"
        
        await  clients[client_id].websocket.send_text(f" stt task real time transcription result 🆘:{transcribe}")


        # append text to text_buffer
        clients[client_id].text_buffer.append(transcribe)

        clients[client_id].stt_queue.task_done()






def audio_chunks(wav, sample_rate, chunk_ms=20):
    samples_per_chunk = sample_rate * chunk_ms  // 1000

    for i in range(0, len(wav), samples_per_chunk):
        yield wav[i:i + samples_per_chunk]


# def tensor_to_pcm16_bytes(wav: "torch.Tensor") -> bytes:
#     audio = wav.detach().cpu().numpy()

#     # if shape (1, N) → (N,)
#     if audio.ndim == 2:
#         audio = audio.squeeze(0)

#     audio = np.clip(audio, -1.0, 1.0)
#     audio_int16 = (audio * 32767).astype(np.int16)

#     return audio_int16.tobytes()

def tensor_to_pcmf32_bytes(wav: "torch.Tensor") -> bytes:
    audio = wav.detach().cpu().numpy()

    # (1, N) → (N,)
    if audio.ndim == 2:
        audio = audio.squeeze(0)

    # ensure float32
    audio = audio.astype(np.float32)

    return audio.tobytes()


def tts_worker(text:str,speaker:str,sr:int):
    wav = tts_model.apply_tts(text=text,speaker=speaker,sample_rate=sr)
    return wav

async def tts_via_process(text: str) -> bytes:
    loop = asyncio.get_running_loop()
    # async with transcription_semaphore:
    try:
            # run_in_executor schedules in the ProcessPoolExecutor (child process)
        result = await loop.run_in_executor(executor, tts_worker,text,"en_5",48000) 
        #tts_model.apply_tts(text=text,speaker="en_5",sample_rate=48000)
        return result
    except Exception as e:
        return f"[tts_via_process_error] {repr(e)}"




async def tts(queue, ws ,client_id):
    try:
        while True:
            text = await queue.get()

            if text == "end":
                queue.task_done()
                break

            # wav = tts_model.apply_tts(
            #     text=text,
            #     speaker="en_5",
            #     sample_rate=48000
            # )
            wav = await tts_via_process(text)
            


            for chunk in audio_chunks(wav, 48000, chunk_ms=20):
                async with clients[client_id].send_lock:
                    chunk_byte = tensor_to_pcmf32_bytes(chunk)
                    await ws.send_bytes(chunk_byte)
                    duration = (len(chunk)/2)/48000
                    await asyncio.sleep(duration)


            await clients[client_id].websocket.send_text(f"tts audio stream 🆘")

            queue.task_done()

    except asyncio.CancelledError:
        print("TTS cancelled → stop streaming immediately")
        raise




async def llm_stream():
    """
    Simulate LLM streaming tokens.
    Replace this with your real LLM stream generator.
    """
    chunks = [
        "Hello Chetan,i am listing ! , what are", 
        " you doing today? I hope",
        " you are fine. Bharat mata",
        " ki jay!",
        "i am feeling good today.",
        "have any question for me ",
        "how can help you."
    ]
    for t in chunks:
        # await asyncio.sleep(0.1)
        yield AIMessage(content=t)

# await for all stt queue task is done and run agent and stream audio chunk wise

async def final_stt_task(client_id):
    #final stt sentence get and run agent and stream speech sentence-vise to client
    tts_queue = asyncio.Queue()
    tts_task = asyncio.create_task(tts(tts_queue,clients[client_id].websocket,client_id))

    try:
        await clients[client_id].stt_queue.join()
        final_text = " ".join(clients[client_id].text_buffer)
            

        buffer = ""
        await  clients[client_id].websocket.send_text(f" final stt task result 🆘:{final_text}")

        print(' ❌❌❌❌❌❌❌❌❌❌ final text is❌❌❌❌❌❌❌❌',final_text)


        if final_text.strip()=="":
            print('final stt is empty ❌❌❌❌❌❌❌❌❌❌ ')
        else:

        # agent run and stream chunk , in stream sentece detect and tts than send audio to client
            async for chunk in llm_stream():
            # async for chunk in llm.astream(final_text):
                print("RAW CHUNK:", repr(chunk))
                buffer += chunk.content

                await  clients[client_id].websocket.send_text(f" llm chunk stream 🆘:{chunk.content}")
    
    
                while True:
                    match = re.search(r"([^.?!]*[.?!])", buffer)
                    if not match:
                        break
    
                    sentence = match.group(1).strip()
                    print("✔ Sentence:", sentence)
                    await tts_queue.put(sentence)
    
                    buffer = buffer[len(sentence):].lstrip()

        if buffer.strip():
            await tts_queue.put(buffer.strip())

        await tts_queue.put("end")
        await tts_task
        await  clients[client_id].websocket.send_text(f" agent speech is end and text_buffer is cleanup")
        clients[client_id].text_buffer = []


    except asyncio.CancelledError:
        print("final_stt_task  and tts_task cancelled")
        tts_task.cancel()
        raise   # VERY IMPORTANT it mean task is cancelled . if not raise it mean it finished normally.


LONG_SILENCE_SECONDS = 2
SHORT_SILENCE_SECONDS = .2
VAD_THRESHOLD_VALUE = .6


                    # 1. when websocket is connect
                    # asyncio.create_task(stt_queue_worker(stt_queue)) run in bg.


                    # 2.vad detect audio than,
                    # it first check asyncio.create_task(final_stt_worker(cliend_id)) is run or not, if run than close first.
                    # than set in_speech=True.
                    # set audio_buffer=[]. and
                    # append audio to audio_buffer. and again continue to while loop using "continue" keyword.


                    # 3.when short silence detect
                    #  put audio buffer into stt queue, than 
                    #  set audio_buffer=[]

                    #stt_queue_worker get audio in byte( b"".join(audio_buffer)) transcribe audio and than append transcribe to tex_buffer and
                    # set stt_queue.task_done().

                    # 4.when long silence
                    # if first check final_stt_worker run or not. if run than cancel first
                    # run asyncio.create_task(final_stt_task(client_id)) and 
                    #set silence_start=None
                    #in_speech=False
                    
                    
                    #5.final_Stt_worker task, 

                    # it first run asyncio.create_task(tts_worker(tts_queue)) in bg
                    # await stt_queue.join() ,wait all full text_buffer.
                    # than use sentence (" ".join(text_buffer) ) to run agent .
                    # agent run in stream mode for real time voice send .
                    # stream chunk in sentence detect and than put sentence in tts_worker 
                    # tts_worker send audio_chunk not full audio sentence.
                    # tts model is not stream but ,you can only play/send the generated WAV in chunks




@app.websocket("/call/{client_id}")
async def audio_ws(ws: WebSocket,client_id:str):
    await ws.accept()
    audio_buffer = []
    in_speech = False
    silence_start = None
    client_id = client_id
    stt_event = asyncio.Event()

    session = ClientSession(
        websocket=ws,
        stt_event=  stt_event,
        send_lock = asyncio.Lock(),
        stt_task= None,
        stt_queue = asyncio.Queue(),
        final_task=None,
        text_buffer=[]
        )

    Conn_Manager.connect(client_id,session)
    print("Client connected", client_id)

    clients[client_id] = session
    
    # stt_queue_worker run all time until websocket is connected
    clients[client_id].stt_task = asyncio.create_task(stt_queue_worker(client_id))
        
    try:
        while True:
            # Expect client to send raw PCM int16 bytes (frames sized for VAD)
            data = await ws.receive_bytes()
            # Guard: if client misbehaves, skip
            if not data:
                continue

            p = vad(data)
            print("VAD:", p)
            # await  clients[client_id].websocket.send_text(f"vad {p}")
            #voice chunk
            if p >= VAD_THRESHOLD_VALUE:
                if not in_speech:
                    #stop final_stt_task if run in bg
        
                    if clients[client_id].final_task != None and  not clients[client_id].final_task.done():
                        clients[client_id].final_task.cancel()
                        await  clients[client_id].websocket.send_text(f" final stt task is cancel ❌❌❌❌❌❌❌❌❌❌❌❌❌❌❌❌❌❌")
                        print('final stt task is cancel ❌❌❌❌❌❌❌❌❌❌❌❌❌❌❌❌❌❌')

                    print("🎤 Speech started 🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢")
                    in_speech = True
                    audio_buffer = []
                

                print('audio buffer when speak 🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤🎤')
                await  clients[client_id].websocket.send_text(f"buffer audio 🟢🟢🟢")

                audio_buffer.append(data)
                silence_start = None
                continue   # here if vad detect audio than it goto start of while loop (continue).  avoid goto next line

            # silence chunk
            if in_speech:
                if silence_start is None:
                    silence_start = time.time()                     

                #long silence 2 sec silence than run
                if time.time() - silence_start >= LONG_SILENCE_SECONDS:
                    await  clients[client_id].websocket.send_text(f"long silence detect")
                    print('✅✅✅✅✅✅✅✅✅✅ Long silence detect and final stt task is run in bg')

                    clients[client_id].final_task = asyncio.create_task(final_stt_task(client_id))
                    in_speech = False
                    silence_start=None
                    continue

                # short silence after every 200ms silence 
                if time.time() - silence_start >= SHORT_SILENCE_SECONDS:
                    await  clients[client_id].websocket.send_text(f"short silence detect")
                    print('✅✅✅✅✅✅✅✅✅✅ Short silence detect')

                    audio_bytes = b"".join(audio_buffer) #convert list of item into byte. 

                    # Trim if too long
                    if len(audio_bytes) > MAX_SAMPLES * 2:  # int16 = 2 bytes/sample
                        audio_bytes = audio_bytes[-(MAX_SAMPLES * 2):]

                    # if len(final_bytes) == 0:
                    #     print("Empty utterance — skip")
                    #     in_speech = False
                    #     audio_buffer = []
                    #     silence_start = None
                    #     continue 
                    
                    if len(audio_buffer)!=0:
                        try:
                            # pcm_float32 = bytes_to_float32(final_bytes)
                            print("🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑 real time audio transcribe put audio into stt_queue")
                            await clients[client_id].stt_queue.put(audio_bytes)
                            audio_buffer = []
                            # asyncio.create_task(send_audio(ws, pcm_float32))

                        except Exception as e:
                            print("prepare send_audio error:",e)

                        # Reset state
                        # in_speech = False  # that do when reached long silence
                        # silence_start = None  # that do when reached long silence, so no more audio_buffer put into stt_queue , and await for final transcribe.
                        # audio_buffer = []  # that do in short silence after audio_buffer put into stt_queue for real time transcribe.

    except Exception as e:
        print("Client disconnected / error:", e)
    finally:
        # cleanup
        await clients[client_id].stt_queue.put(None)
        # await clients[client_id].stt_task
        if clients[client_id].final_task is not None  and  not clients[client_id].final_task.done():
            clients[client_id].final_task.cancel()

        clients[client_id].stt_task.cancel()
        clients.pop(client_id, None)
        print("Cleaned up client", client_id)





#     Scheduler related

#  AGENT run on shedule task 



async def user_task(user_id:str,message:str):
    if not Conn_Manager.is_connected(user_id):
        print(f"{user_id} is not connected skip task run or paused task")
        return 
    
    print(f"running task for {user_id}")
    await Conn_Manager.send(user_id,message)



#for check new email and response
@app.post('/interval/{client_id}')
async def schedule(req:Request,client_id:str):
    try:
        data = await req.json()
        print(data)
        print('client id ',client_id)

        trigger = IntervalTrigger(seconds=data['sec'])
        job_id = f"{data['client_id']}_{uuid4()}"
        print(job_id)
        scheduler.add_job(
            user_task,
            trigger=trigger,
            args=[data['client_id'],data['message']],
            id=job_id
            )
        return {"interval schedule task is add":data}
    except:
        print('error')
        return {"error":"error"}
            

#run on fix time
@app.post('/cron_schedule')
async def schedule(req:Request):
    try:
        data =await req.json()
        print('data',data)
        return {"cron schedule task is done":data}
        run_time = datetime.now() + timedelta(minutes=5)
        scheduler.add_job(
            my_job,
            trigger="date",
            run_date=run_time
            )
    except:
        print('error')
        return {"error":"error"}
            


@app.get("/jobs/{user_id}")
async def list_jobs(user_id:str):
    jobs = []
    for job in scheduler.get_jobs():
        if job.id.startswith(user_id):
            print(job)
            jobs.append({
                "id": job.id,
                "next_run_time": str(job.next_run_time),
                "trigger": str(job.trigger),
                })
    
    return jobs



@app.delete("/jobs/{job_id}")
async def remove_job(job_id: str):
    scheduler.remove_job(job_id)
    return {"message": "job removed", "job_id": job_id}













# vector indexing  and search query
import chromadb
client = chromadb.Client()
collection = client.get_or_create_collection(name="documents")





# python mongodb








app.mount("/static", StaticFiles(directory=f"{react_build_dir}/static"), name="static")

app.mount("/", StaticFiles(directory=react_build_dir, html=True), name="react")

