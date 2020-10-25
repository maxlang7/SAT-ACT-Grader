import hashlib
import json
import shutil
import os
#email sending stuff
import smtplib
import tempfile
from datetime import date
from email.message import EmailMessage

import flask
import pyodbc
import requests
from celery import Celery
from dotenv import load_dotenv

import grader as g

flaskapp = flask.Flask(__name__)
celeryapp = Celery('tasks', broker='pyamqp://guest@localhost//')
flaskapp.config["DEBUG"] = True
load_dotenv()
DB_SERVER_NAME=os.getenv('DB_SERVER_NAME')
DB_USER=os.getenv('DB_USER')
DB_PASSWORD=os.getenv('DB_PASSWORD')

#TODO: add database connection details
#TODO: set up celery (+rabbit mq) on server
database_connection = ""
#TODO Jason questions:Get SMtp server info. Database connection info.
#adminvpt@studypoint.com


#uploads parsed test data to database
def upload_to_database(examinfo, page_answers):
    conn = pyodbc.connect('Driver={SQL Server};'
                      f'Server={DB_SERVER_NAME};'
                      f'Database={DB_NAME};'
                      f'UID={DB_USER};'
                      f'PWD={DB_PASSWORD};'
                      'Trusted_Connection=yes;')

    cursor = conn.cursor()
    cursor.execute("insert into Grader_Submissions "\
                   "(First_Name, Last_Name, Email_Address, Test_Type, Test_ID, Submission_JSON) "  \
                   "values (?,?,?,?,?,?)", examinfo['First Name'], examinfo['Last Name'], 
                   examinfo['Email'], examinfo['Test'], examinfo['Test ID'], json.dumps(page_answers))
    
    submission_id = cursor.lastrowid()

    for page, pagecounter in enumerate(page_answers):
        for answer, qcounter in enumerate(page):
            cursor.execute("insert into Grader_Submission_Answers "\
                        "(Submission_ID, Test_Section, Test_Question_Number, Test_Question_Answer)" \
                        " values (?,?,?,?)", submission_id, pagecounter+1, 
                        calculate_qnum(examinfo['Test'], pagecounter, qcounter),
                        answer)

    conn.commit()

#pages don't all start with 1, so we need to handle that situation
def calculate_qnum(test,pagecounter,qcounter):
    #need to calculate the question number on the test e.g. page 5 qcounter 1 = 31
    if test == 'sat':
        if pagecounter == 3:
            return qcounter +16 # box 2 on page 3 starts with 16
        elif pagecounter == 5:
            return qcounter + 31 # box 1 on page 5 starts with 31
        elif pagecounter == 6:
            return qcounter + 36 # box 2 on page 5 starts with 36
        else:
            return qcounter + 1 # otherwise, the first question on the page is 1
    else:
        return qcounter +1

def download_image(imgurl, imgpath):
    #dowloads the imgurl and writes it into imgpath.
    r = requests.get(imgurl, stream = True) 
    if r.status_code == 200:
        r.raw.decode_content=True
        shutil.copyfileobj(r.raw, imgpath)
        return True
    else:
        return False

@celeryapp.task
def grade_test(examinfo):
    errors = []
    imgurls = examinfo['Image Urls']
    test = examinfo['Test']
    email = examinfo['Email']
    page_answers = []
    for i, imgurl in enumerate(imgurls):
        page = i + 1
        if page == 3 or page == 5:
            boxes = [1,2]
        else:
            boxes = [1]
        for box in boxes:
            with tempfile.TemporaryFile() as imgpath:
                download_success = download_image(imgurl, imgpath)
                if download_success:
                    grader = g.Grader()
                    jsonData = grader.grade(imgpath, False, False, 1.0, test.lower(), box, page)
                    data = json.loads(jsonData)
                    print(data['answer']['bubbled'])
                    if data['status'] == 0:
                        page_answers.append(data['answer']['bubbled'])
                    else:
                        errors.append(data['error'])
                else:
                    errors.append('Unable to download {imgurl}')
    if len(errors) > 0:
        send_error_message(email, errors)
    else:
        upload_to_database(examinfo, page_answers)

def send_error_message(email, errors):
    msg = EmailMessage()
    msg['Subject'] = 'We had trouble grading your recent test.'
    msg['From'] = 'grader@studypoint.com'
    msg['To'] = email
    msg.set_content('Unable to process test. Errors:' + errors.join('\n'))
    s = smtplib.SMTP('localhost')
    s.send_message(msg)
    s.quit()

def examinfohash(examinfo):
    # makes a hash of everything except the imageurls in examinfo so we can identify student submissions
    dict = {k: v for k, v in examinfo.items() if not k == 'Image Urls'}.keys()
    dict['Date Submitted'] = date.today
    return hashlib.sha1(json.dumps(dict, sort_keys=True)).hexdigest()

@flaskapp.route('/v1/grader', methods=['POST'])
def handle_grader_message():
    #TODO: determine and parse POSTed message
    imageurls = []
    print(flask.request.json)
    for i in [8,9,10,11,12]:
        imageurls.append(flask.request.json[f'Field{i}_url'])
    #for jason
    examinfo = {
    'First Name': flask.request.json['Field1'],
    'Last Name': flask.request.json['Field2'], 
    'Email': flask.request.json['Field5'], 
    'Test': flask.request.json['Field6'],
    'Image Urls': imageurls
    }
    examinfo['Test ID'] = examinfohash(examinfo)
    grade_test.delay(examinfo)
    return flask.Response(status=202)

@flaskapp.route('/', methods=['GET'])
def home():
    return "<h1>Grader API</h1><p.>This site is a API Portal for AutoGrader</p>"
if __name__ == "__main__":
    flaskapp.run(host='0.0.0.0', port=8000)