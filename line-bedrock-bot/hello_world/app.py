import os
import json
import time
import boto3
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# 1. 初始化
line_bot_api = LineBotApi(os.environ['LINE_CHANNEL_ACCESS_TOKEN'])
handler = WebhookHandler(os.environ['LINE_CHANNEL_SECRET'])
bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')
db = boto3.resource('dynamodb')
table = db.Table(os.environ['TABLE_NAME'])

def lambda_handler(event, context):
    signature = event['headers'].get('x-line-signature') or event['headers'].get('X-Line-Signature')
    body = event['body']

    try:
        # handler 會自動觸發下方的 handle_message
        handler.handle(body, signature)
    except InvalidSignatureError:
        return {'statusCode': 400, 'body': 'Invalid Signature'}
    except Exception as e:
        print(f"Error: {e}")
        return {'statusCode': 500, 'body': str(e)}

    return {'statusCode': 200, 'body': 'OK'}

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_msg = event.message.text
    reply_token = event.reply_token

    # A. 讀取歷史紀錄
    response = table.get_item(Key={'UserId': user_id})
    history = response.get('Item', {}).get('Messages', [])

    # B. 準備傳給 Bedrock 的訊息
    history.append({"role": "user", "content": user_msg})
    # 只保留最近 10 輪對話以控制 Token 消耗
    current_context = history[-10:]

    # C. 呼叫 Bedrock (Claude)
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1000,
        "system": "你是一個專業且親切的台灣 AI 助手，請使用繁體中文回答。",
        "messages": current_context
    })

    try:
        res = bedrock.invoke_model(
            modelId='us.anthropic.claude-sonnet-4-20250514-v1:0',
            body=body
        )
        res_body = json.loads(res['body'].read())
        ai_reply = res_body['content'][0]['text']

        # D. 更新記憶並存回 DynamoDB (包含 TTL 24小時)
        current_context.append({"role": "assistant", "content": ai_reply})
        table.put_item(Item={
            'UserId': user_id,
            'Messages': current_context,
            'ExpireTime': int(time.time()) + 86400
        })

        # E. 回覆 LINE
        line_bot_api.reply_message(reply_token, TextSendMessage(text=ai_reply))

    except Exception as e:
        print(f"AI Error: {e}")
        line_bot_api.reply_message(reply_token, TextSendMessage(text="抱歉，我現在有點頭痛，請稍後再試！"))