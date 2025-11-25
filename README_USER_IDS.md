# Instagram User ID Fetcher & Batch Story Processor

כלים לקבלת מזהי משתמשים של משפיעניות והרצת `stories_with_stickers.php` עבור מספר חשבונות.

## 📋 תוכן עניינים

1. [get_user_ids.php](#get_user_idsphp) - קבלת IDs מרשימת שמות משתמש
2. [batch_stories.php](#batch_storiesphp) - הרצה מרובה של stories_with_stickers
3. [דוגמאות שימוש](#דוגמאות-שימוש)

---

## 🔍 get_user_ids.php

סקריפט לקבלת מזהי משתמשים (User IDs) מרשימת שמות משתמש באינסטגרם.

### שימוש

#### אופן 1: שמות משתמש מהשורה

```bash
php get_user_ids.php username1 username2 username3
```

#### אופן 2: קריאה מקובץ

```bash
php get_user_ids.php --file influencers.txt
```

### פורמט קובץ influencers.txt

```text
# רשימת משפיעניות
username1
username2
username3
# שורות עם # הן הערות ויתעלמו
```

### פלט

הסקריפט יציג:
- ✅ טבלה עם שמות משתמש, IDs, שמות מלאים ומספר עוקבים
- 📋 רשימת IDs בלבד (לשימוש בסקריפטים אחרים)
- 💾 קובץ JSON עם כל המידע: `user_ids_YYYY-MM-DD_HHMMSS.json`
- 📝 פקודות מוכנות להרצה של `stories_with_stickers.php`

### דוגמת פלט

```
Fetching user IDs for 3 username(s)...

Processing: @username1 ... ✓ ID: 123456789
Processing: @username2 ... ✓ ID: 987654321
Processing: @username3 ... ✓ ID: 555666777

============================================================
RESULTS
============================================================

Successfully fetched 3 user ID(s):

Username             User ID         Full Name                      Followers
----------------------------------------------------------------------------------------------------
@username1           123456789       Jane Doe                       150,000
@username2           987654321       John Smith                     250,000 🔒
@username3           555666777       Sarah Cohen                    75,000

------------------------------------------------------------
User IDs only (for batch processing):
123456789
987654321
555666777

------------------------------------------------------------
Command to run stories_with_stickers.php for each user:

php stories_with_stickers.php 123456789  # @username1
php stories_with_stickers.php 987654321  # @username2
php stories_with_stickers.php 555666777  # @username3

✓ Results saved to: user_ids_2025-11-25_023300.json
```

---

## 🔄 batch_stories.php

סקריפט להרצה אוטומטית של `stories_with_stickers.php` עבור מספר משתמשים ושילוב התוצאות.

### שימוש

#### אופן 1: IDs מהשורה

```bash
php batch_stories.php 123456789 987654321 555666777
```

#### אופן 2: קריאה מקובץ

```bash
php batch_stories.php --file user_ids.txt
```

### פורמט קובץ user_ids.txt

```text
# רשימת User IDs
123456789
987654321
555666777
```

### פלט

הסקריפט יציג:
- 📊 סטטיסטיקות: כמה משתמשים, כמה סטוריז, כמה שגיאות
- 👥 פירוט לפי משתמש
- 💾 קובץ JSON משולב: `batch_stories_YYYY-MM-DD_HHMMSS.json`

### דוגמת פלט

```
Processing stories for 3 user(s)...

Fetching stories for user ID: 123456789 ... ✓ Found 5 story/stories
Fetching stories for user ID: 987654321 ... ✓ Found 3 story/stories
Fetching stories for user ID: 555666777 ... ✓ Found 8 story/stories

============================================================
SUMMARY
============================================================

Total users processed: 3
Total stories found: 16
Total errors: 0

Stories by user:
  • @username1 (ID: 123456789): 5 story/stories
  • @username2 (ID: 987654321): 3 story/stories
  • @username3 (ID: 555666777): 8 story/stories

✓ Combined results saved to: batch_stories_2025-11-25_023500.json
```

---

## 📚 דוגמאות שימוש

### תרחיש 1: מרשימת שמות משתמש לסטוריז

```bash
# שלב 1: הכנת קובץ עם שמות משתמש
echo "username1" > influencers.txt
echo "username2" >> influencers.txt
echo "username3" >> influencers.txt

# שלב 2: קבלת IDs
php get_user_ids.php --file influencers.txt

# שלב 3: העתקת IDs לקובץ חדש (ידנית או אוטומטית)
# או שימוש ישיר בפלט

# שלב 4: הרצת batch stories
php batch_stories.php --file user_ids.txt
```

### תרחיש 2: תהליך מהיר עם משתמשים ספציפיים

```bash
# קבלת IDs
php get_user_ids.php influencer1 influencer2 influencer3

# העתקת IDs מהפלט והרצת batch
php batch_stories.php 123456789 987654321 555666777
```

### תרחיש 3: שימוש ב-PowerShell לאוטומציה מלאה

```powershell
# הרצת get_user_ids וקבלת הפלט
$output = php get_user_ids.php --file influencers.txt

# חילוץ IDs מהפלט (בין "User IDs only" ל"Command to run")
# והרצת batch_stories

# או שימוש בקובץ JSON שנוצר
$json = Get-Content "user_ids_*.json" | ConvertFrom-Json
$ids = $json.results | ForEach-Object { $_.user_id }
$idsString = $ids -join " "

# הרצת batch
php batch_stories.php $idsString
```

---

## 🔧 דרישות

### משתני סביבה נדרשים

```bash
# Windows (PowerShell)
$env:IG_SESSIONID = "your_session_id"
$env:IG_CSRF = "your_csrf_token"
$env:IG_DS_USER_ID = "your_user_id"

# אופציונלי
$env:IG_UA = "Mozilla/5.0..."
$env:TESSERACT_PATH = "C:\Program Files\Tesseract-OCR\tesseract.exe"
$env:FFMPEG_PATH = "C:\ffmpeg\bin\ffmpeg.exe"
$env:OCR_LANGS = "heb+eng"
```

### תלויות

- PHP 7.4+
- Composer dependencies (מותקנים ב-`vendor/`)
- GuzzleHTTP

---

## 📁 קבצים שנוצרים

### user_ids_*.json

```json
{
  "timestamp": "2025-11-25T02:33:00+02:00",
  "total_requested": 3,
  "total_found": 3,
  "total_errors": 0,
  "results": [
    {
      "username": "username1",
      "user_id": "123456789",
      "full_name": "Jane Doe",
      "is_private": false,
      "followers": 150000
    }
  ],
  "errors": []
}
```

### batch_stories_*.json

```json
{
  "timestamp": "2025-11-25T02:35:00+02:00",
  "total_users": 3,
  "total_stories": 16,
  "total_errors": 0,
  "by_user": [
    {
      "user_id": "123456789",
      "username": "username1",
      "stories": [...]
    }
  ],
  "all_stories": [...],
  "errors": []
}
```

---

## ⚠️ הערות חשובות

1. **Rate Limiting**: הסקריפטים כוללים השהיות (delays) כדי למנוע חסימה מאינסטגרם
2. **חשבונות פרטיים**: חשבונות פרטיים מסומנים ב-🔒 - תצטרכי לעקוב אחריהם כדי לראות סטוריז
3. **שגיאות**: כל השגיאות נשמרות בקובץ JSON לבדיקה מאוחר יותר
4. **Supabase**: אם מוגדר SUPABASE_URL, הסטוריז יישמרו גם ב-DB

---

## 🆘 פתרון בעיות

### שגיאה: "Missing env: IG_CSRF / IG_SESSIONID / IG_DS_USER_ID"

ודאי שהגדרת את משתני הסביבה:

```powershell
$env:IG_SESSIONID = "..."
$env:IG_CSRF = "..."
$env:IG_DS_USER_ID = "..."
```

### שגיאה: "HTTP 401" או "HTTP 403"

- Session פג תוקף - צריך להתחבר מחדש לאינסטגרם ולעדכן את ה-cookies
- CSRF token לא תקין

### שגיאה: "User ID not found"

- שם המשתמש לא קיים
- שם המשתמש שונה (בדקי אותיות גדולות/קטנות)
- החשבון נמחק או מושעה

---

## 📞 תמיכה

לשאלות או בעיות, בדקי את:
1. קובץ ה-JSON עם השגיאות
2. הפלט של הסקריפט (STDERR)
3. קובץ `story_debug.json` (אם `IG_DEBUG=1`)
