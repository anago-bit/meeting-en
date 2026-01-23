import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google import genai

# --- 環境変数（GitHub Secretsから取得） ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SOURCE_FOLDER_ID = os.environ.get("SOURCE_FOLDER_ID")
TARGET_FOLDER_ID = os.environ.get("TARGET_FOLDER_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("SERVICE_ACCOUNT_JSON")
# 検索キーワードもSecretsから取得
SEARCH_KEYWORD = os.environ.get("SEARCH_KEYWORD")

# 権限範囲
SCOPES = [
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive'
]

def get_credentials():
    if not SERVICE_ACCOUNT_JSON:
        raise ValueError("環境変数 SERVICE_ACCOUNT_JSON が未設定です。")
    info = json.loads(SERVICE_ACCOUNT_JSON)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

def find_and_move_latest_meeting_doc():
    """SOURCEフォルダからキーワードに合う最新ドキュメントを探してTARGETフォルダへ移動"""
    creds = get_credentials()
    drive_service = build('drive', 'v3', credentials=creds)
    
    # name contains で部分一致検索。trashed = false でゴミ箱を除外。
    query = (
        f"'{SOURCE_FOLDER_ID}' in parents and "
        f"name contains '{SEARCH_KEYWORD}' and "
        f"mimeType = 'application/vnd.google-apps.document' and "
        f"trashed = false"
    )
    
    results = drive_service.files().list(
        q=query, 
        orderBy="modifiedTime desc", 
        pageSize=1, 
        fields="files(id, name, parents)"
    ).execute()
    
    files = results.get('files', [])
    
    if not files:
        print(f"情報: 題名に「{SEARCH_KEYWORD}」を含む新しい議事録は見つかりませんでした。")
        return None, None

    target_file = files[0]
    file_id = target_file['id']
    file_name = target_file['name']

    # まだターゲットフォルダにいない場合のみ移動を実行
    if TARGET_FOLDER_ID not in target_file.get('parents', []):
        print(f"🔒 セキュリティ隔離: 「{file_name}」を専用フォルダへ移動します。")
        previous_parents = ",".join(target_file.get('parents'))
        drive_service.files().update(
            fileId=file_id,
            addParents=TARGET_FOLDER_ID,
            removeParents=previous_parents,
            fields='id, parents'
        ).execute()
    
    return file_id, file_name

def read_doc(doc_id):
    """Googleドキュメントの本文を抽出（表の中のテキストも含む）"""
    creds = get_credentials()
    service = build('docs', 'v1', credentials=creds)
    document = service.documents().get(documentId=doc_id).execute()
    
    text = ""
    def extract_text(elements):
        content = ""
        for value in elements:
            if 'textRun' in value:
                content += value.get('textRun').get('content')
            if 'inlineObjectElement' in value:
                pass # 画像などはスキップ
        return content

    # ドキュメント全体の構造をループ
    for body_content in document.get('body').get('content'):
        if 'paragraph' in body_content:
            text += extract_text(body_content.get('paragraph').get('elements'))
        elif 'table' in body_content:
            # 表（文字起こしが表形式の場合があるため）の中も読み取る
            for row in body_content.get('table').get('tableRows'):
                for cell in row.get('tableCells'):
                    for cell_content in cell.get('content'):
                        if 'paragraph' in cell_content:
                            text += extract_text(cell_content.get('paragraph').get('elements'))
    return text

def translate_full_text(text):
    """Geminiによる一字一句翻訳（Googleの定型文を無視する指示を追加）"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
    あなたはプロの翻訳者です。
    送付するテキストはGoogle Meetの議事録ドキュメントです。

    【重要な注意点】
    1. ドキュメントの冒頭に「要約は生成されませんでした」や「文字起こしを確認できます」といったGoogleのシステムメッセージが含まれている場合がありますが、これらは無視してください。
    2. その後に続く「実際の会話の内容（文字起こし）」を探し、それを翻訳対象としてください。
    3. 内容を要約したり省略したりせず、すべての発言を一字一句網羅して翻訳してください。
    4. 出力は「英語：」「ネパール語：」と分けて記述してください。

    議事録テキスト:
    {text}
    """
    
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )
    return response.text

def translate_full_text(text):
    """Geminiによる一字一句翻訳"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
    以下の議事録を、内容を省略したり要約したりせずに、原文のすべての発言を網羅して翻訳してください。
    出力は「英語」と「ネパール語」の両方で行ってください。

    【ルール】
    - 要約は一切禁止です。
    - 全ての発言を一字一句漏らさず翻訳してください。
    - 形式は、日本語原文に対応するように「英語：」「ネパール語：」と分けて記述してください。

    議事録内容:
    {text}
    """
    
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )
    return response.text

def create_translated_doc(folder_id, original_name, translated_text):
    """翻訳済みドキュメントを作成し、指定フォルダに格納"""
    creds = get_credentials()
    docs_service = build('docs', 'v1', credentials=creds)
    drive_service = build('drive', 'v3', credentials=creds)

    title = f"【翻訳完了】{original_name}"
    doc = docs_service.documents().create(body={'title': title}).execute()
    doc_id = doc.get('documentId')

    # テキスト書き込み
    requests = [{'insertText': {'location': {'index': 1}, 'text': translated_text}}]
    docs_service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute()

    # 作成されたドキュメントをターゲットフォルダへ移動
    file = drive_service.files().get(fileId=doc_id, fields='parents').execute()
    drive_service.files().update(
        fileId=doc_id, 
        addParents=folder_id, 
        removeParents=",".join(file.get('parents'))
    ).execute()
    
    return doc_id

if __name__ == "__main__":
    try:
        if not all([GEMINI_API_KEY, SOURCE_FOLDER_ID, TARGET_FOLDER_ID, SEARCH_KEYWORD]):
            print("エラー: 必要な環境変数(Secrets)が不足しています。")
        else:
            print(f">>> 1. 「{SEARCH_KEYWORD}」の検索と仕分けを開始...")
            target_id, target_name = find_and_move_latest_meeting_doc()
            
            if target_id:
                print(f">>> 2. 対象ファイルを読み込み中: {target_name}")
                content = read_doc(target_id)
                
                print(">>> 3. Geminiで翻訳を実行中（要約禁止・一字一句）...")
                translated_result = translate_full_text(content)
                
                print(">>> 4. 翻訳ドキュメントを作成中...")
                new_id = create_translated_doc(TARGET_FOLDER_ID, target_name, translated_result)
                
                print(f"\n✅ 完了！専用フォルダ(ID:{TARGET_FOLDER_ID})に保存しました。")
                print(f"URL: https://docs.google.com/document/d/{new_id}/edit")
            else:
                print("条件に合うファイルがなかったため、処理を終了します。")
        
    except Exception as e:
        print(f"❌ エラーが発生しました: {e}")
