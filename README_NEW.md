# Instagram Stories Scraper

פרויקט לחילוץ וניתוח סטוריז מאינסטגרם עם זיהוי קופונים, קישורים ומותגים.

## 📋 מה הפרויקט עושה?

1. **מושך סטוריז** של משפיעניות מאינסטגרם
2. **מזהה קופונים** וקודי הנחה
3. **מחלץ קישורים** לאתרי מותגים
4. **מנתח תוכן** עם OCR (זיהוי טקסט בתמונות)
5. **שומר הכל ב-Supabase** למעקב ודיווח

## 🚀 התקנה מהירה

### דרישות מקדימות
- PHP 7.2+
- Python 3.13+
- Composer
- uv (Python package manager)

### התקנת תלויות

```powershell
# התקנת תלויות PHP
composer install

# התקנת תלויות Python
uv sync
```

## 🔧 הגדרת משתני סביבה

ערכי את קובץ `.env` עם הפרטים שלך:

```env
IG_SESSIONID=your_session_id
IG_DS_USER_ID=your_user_id
IG_CSRF=your_csrf_token
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_KEY=your_service_key
OPENAI_API_KEY=your_openai_key
```

## 📖 שימוש

### 1. קבלת IDs של משפיעניות

ערכי את `influencers.txt` עם שמות המשתמשים:

```text
username1
username2
username3
```

הריצי:

```powershell
php get_user_ids.php --file influencers.txt
```

תקבלי טבלה עם IDs וקובץ JSON עם כל המידע.

### 2. משיכת סטוריז של משפיענית אחת

```powershell
php stories_with_stickers.php USER_ID
```

### 3. משיכת סטוריז של מספר משפיעניות

```powershell
php batch_stories.php --file user_ids.txt
```

### 4. הרצה יומית אוטומטית

```powershell
python run_daily.py
```

הסקריפט:
- מושך סטוריז חדשות
- מנתח אותן עם AI (GPT-4)
- מזהה קופונים וקישורים
- שומר ב-Supabase

## 📁 מבנה הפרויקט

```
instagram-stories-clean/
├── stories_with_stickers.php  # סקריפט ראשי למשיכת סטוריז
├── run_daily.py               # הרצה יומית + ניתוח AI
├── get_user_ids.php           # כלי לקבלת IDs
├── batch_stories.php          # הרצה מרובה
├── influencers.txt            # רשימת משפיעניות
├── composer.json              # תלויות PHP
├── pyproject.toml             # תלויות Python
├── .env                       # משתני סביבה
├── .gitignore                 # קבצים להתעלמות
├── README.md                  # התיעוד הזה
└── README_USER_IDS.md         # תיעוד מפורט על get_user_ids
```

## 🔍 פלטים

הסקריפטים יוצרים קבצי JSON:

- `user_ids_YYYY-MM-DD_HHMMSS.json` - IDs של משפיעניות
- `batch_stories_YYYY-MM-DD_HHMMSS.json` - סטוריז משולבות

## 📚 תיעוד נוסף

- [README_USER_IDS.md](README_USER_IDS.md) - הסבר מפורט על כלי ה-IDs

## ⚙️ תכונות מתקדמות

### OCR (זיהוי טקסט בתמונות)

להפעלת OCR, הורידי Tesseract והגדירי ב-`.env`:

```env
TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe
OCR_LANGS=heb+eng
```

### עיבוד וידאו

להפעלת OCR על וידאו, הורידי FFmpeg והגדירי:

```env
FFMPEG_PATH=C:\ffmpeg\bin\ffmpeg.exe
```

## 🆘 פתרון בעיות

### שגיאה: "Missing env: IG_CSRF / IG_SESSIONID"

ודאי שהגדרת את כל משתני הסביבה בקובץ `.env`

### שגיאה: "HTTP 401" או "HTTP 403"

Session פג תוקף - התחברי מחדש לאינסטגרם ועדכני את ה-cookies

### שגיאה: "composer: command not found"

התקיני Composer מ-https://getcomposer.org/

## 📄 רישיון

MIT License - ראי קובץ LICENSE בפרויקט המקורי
