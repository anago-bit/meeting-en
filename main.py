import os
import json
import smtplib
import requests
from email.mime.text import MIMEText
from email.utils import formatdate
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google import genai

# --- 環境変数 ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SOURCE_FOLDER_ID = os.environ.get("SOURCE_FOLDER_ID")
TARGET_FOLDER_ID = os.environ.get("TARGET_FOLDER_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("SERVICE_ACCOUNT_JSON")
SEARCH_KEYWORD = os.environ.get("SEARCH_KEYWORD")

# メール設定
MAIL_ADDRESS = os.environ.get("MAIL_ADDRESS")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
MAIL_SMTP_SERVER = os.environ.get("MAIL_SMTP_SERVER")
MAIL_SMTP_PORT = os.environ.get("MAIL_SMTP_PORT")
TALKNOTE_POST_EMAIL = os.environ.get("TALKNOTE_POST_EMAIL")

SCOPES = ['https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive']

def get_credentials():
    info = json.loads(SERVICE_ACCOUNT_JSON)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

def find_latest_doc():
    """未処理の最新ドキュメントを探す（【処理済】を含まないもの）"""
    service = build('drive', 'v3', credentials=get_credentials())
    # クエリに「not name contains '【処理済】'」を追加
    query = (
        f"'{SOURCE_FOLDER_ID}' in parents and "
        f"name contains '{SEARCH_KEYWORD}' and "
        f"not name contains '【処理済】' and "
        f"mimeType = 'application/vnd.google-apps.document' and "
        f"trashed = false"
    )
    results = service.files().list(
        q=query, 
        orderBy="modifiedTime desc", 
        pageSize=1, 
        fields="files(id, name)", 
        includeItemsFromAllDrives=True, 
        supportsAllDrives=True
    ).execute()
    files = results.get('files', [])
    return files[0] if files else None

def read_doc(doc_id):
    service = build('docs', 'v1', credentials=get_credentials())
    document = service.documents().get(documentId=doc_id).execute()
    text = ""
    for content in document.get('body').get('content'):
        if 'paragraph' in content:
            for element in content.get('paragraph').get('elements'):
                text += element.get('textRun', {}).get('content', '')
        elif 'table' in content:
            for row in content.get('table').get('tableRows'):
                for cell in row.get('tableCells'):
                    for cell_content in cell.get('content'):
                        if 'paragraph' in cell_content:
                            for element in cell_content.get('paragraph').get('elements'):
                                text += element.get('textRun', {}).get('content', '')
    return text

def translate_full_text(text):
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"以下の議事録を一字一句漏らさず英語とネパール語に翻訳してください。要約禁止。\n\n{text}"
    response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
    return response.text

def create_translated_doc(original_name, translated_text):
    drive_service = build('drive', 'v3', credentials=get_credentials())
    docs_service = build('docs', 'v1', credentials=get_credentials())
    title = f"【翻訳完了】{original_name}"
    file_metadata = {'name': title, 'mimeType': 'application/vnd.google-apps.document', 'parents': [TARGET_FOLDER_ID]}
    file = drive_service.files().create(body=file_metadata, fields='id', supportsAllDrives=True).execute()
    doc_id = file.get('id')
    requests_list = [{'insertText': {'location': {'index': 1}, 'text': translated_text}}]
    docs_service.documents().batchUpdate(documentId=doc_id, body={'requests': requests_list}).execute()
    return doc_id, title

def copy_original_file(file_id, original_name):
    drive_service = build('drive', 'v3', credentials=get_credentials())
    try:
        copy_metadata = {'name': f"【原本】{original_name}", 'parents': [TARGET_FOLDER_ID]}
        drive_service.files().copy(fileId=file_id, body=copy_metadata, supportsAllDrives=True).execute()
        print(f">>> 共有ドライブへ原本をコピーしました")
    except Exception as e:
        print(f"⚠️ 原本のコピー失敗: {e}")

def rename_original_file(file_id, original_name):
    """原本の名前に【処理済】を付与して重複を防止する"""
    drive_service = build('drive', 'v3', credentials=get_credentials())
    try:
        new_name = f"【処理済】{original_name}"
        drive_service.files().update(
            fileId=file_id, 
            body={'name': new_name},
            supportsAllDrives=True
        ).execute()
        print(f">>> 原本を「{new_name}」にリネームしました")
    except Exception as e:
        print(f"⚠️ リネーム失敗: {e}")

def send_email_notification(title, doc_url):
    if not all([MAIL_ADDRESS, MAIL_PASSWORD, TALKNOTE_POST_EMAIL]):
        print("メール設定不足のため送信スキップ")
        return
    
    subject = f"翻訳完了: {title}"
    body = f"自動翻訳が完了しました。\n\n{title}\n{doc_url}"
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = MAIL_ADDRESS
    msg['To'] = TALKNOTE_POST_EMAIL
    msg['Date'] = formatdate(localtime=True)

    try:
        if MAIL_SMTP_PORT == "465":
            server = smtplib.SMTP_SSL(MAIL_SMTP_SERVER, int(MAIL_SMTP_PORT), timeout=20)
        else:
            server = smtplib.SMTP(MAIL_SMTP_SERVER, int(MAIL_SMTP_PORT), timeout=20)
            server.starttls()
        server.login(MAIL_ADDRESS, MAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("✅ Talknoteメール送信成功")
    except Exception as e:
        print(f"❌ メール送信エラー: {e}")

if __name__ == "__main__":
    try:
        target_file = find_latest_doc()
        if target_file:
            orig_id = target_file['id']
            orig_name = target_file['name']
            print(f">>> 処理開始: {orig_name}")
            
            content = read_doc(orig_id)
            translated = translate_full_text(content)
            
            # 1. 翻訳ドキュメント作成
            new_id, new_title = create_translated_doc(orig_name, translated)
            # 2. 共有ドライブへ原本をコピー
            copy_original_file(orig_id, orig_name)
            # 3. ★原本の名前を変更（これが二重実行のストッパーになります）
            rename_original_file(orig_id, orig_name)
            
            url = f"https://docs.google.com/document/d/{new_id}/edit"
            print(f"✅ 全工程完了 URL: {url}")
            send_email_notification(new_title, url)
        else:
            print("新規の未処理ファイルは見つかりませんでした。")
    except Exception as e:
        print(f"❌ 致命的エラー: {e}")
