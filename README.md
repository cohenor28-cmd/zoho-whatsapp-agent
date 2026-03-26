# Zoho CRM WhatsApp Agent

סוכן חכם שמקבל פקודות בוואטסאפ ומבצע פעולות ב-Zoho CRM.

## פריסה ב-Render.com

### משתני סביבה נדרשים (Environment Variables):

| שם המשתנה | תיאור |
|---|---|
| `TWILIO_ACCOUNT_SID` | מזהה חשבון Twilio |
| `TWILIO_AUTH_TOKEN` | Auth Token של Twilio |
| `TWILIO_WHATSAPP_FROM` | מספר WhatsApp של Twilio (ברירת מחדל: whatsapp:+14155238886) |
| `ZOHO_CLIENT_ID` | Client ID של Zoho API |
| `ZOHO_CLIENT_SECRET` | Client Secret של Zoho API |
| `ZOHO_REFRESH_TOKEN` | Refresh Token של Zoho API |
| `ZOHO_API_DOMAIN` | דומיין Zoho API (ברירת מחדל: https://www.zohoapis.com) |
| `OPENAI_API_KEY` | מפתח OpenAI API |

### הוראות פריסה:
1. צור חשבון ב-render.com
2. לחץ "New Web Service"
3. חבר את ה-GitHub repo
4. הגדר את משתני הסביבה לעיל
5. לחץ Deploy
6. קבל URL קבוע (למשל: https://zoho-agent.onrender.com)
7. עדכן את ה-Webhook ב-Twilio לכתובת: https://YOUR-APP.onrender.com/webhook

## שימוש

שלח פקודות בעברית דרך WhatsApp:

- **יצירת חשבונית**: `050 לטייה של איציק`
- **תשלום**: `טונגצאי בוי שער דוד שילם 120 מזומן`
- **שאילתה**: `כמה חשבוניות פתוחות לאילן?`
