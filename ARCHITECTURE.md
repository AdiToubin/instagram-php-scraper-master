# Instagram Stories Analysis - Architecture

## System Overview

This document describes the architecture of the Instagram Stories analysis system.

## Architecture Diagram

```mermaid
flowchart TB
    subgraph Input["📥 Input Layer"]
        influencers[("influencers.txt<br/>רשימת משפיעניות")]
        user_ids[("user_ids_*.json<br/>מיפוי ID ← Username")]
    end

    subgraph Fetch["🔄 Data Collection"]
        get_ids["get_user_ids.php<br/>המרת שמות ל-IDs"]
        stories_php["stories_with_stickers.php<br/>שליפת סטוריז + OCR"]
        run_daily_stories["run_daily_stories.py<br/>אוטומציה יומית"]
    end

    subgraph Processing["🤖 AI Processing"]
        run_daily["run_daily.py<br/>עיבוד וסיווג"]
        openai["OpenAI GPT-4o-mini<br/>Vision + Text Analysis"]
    end

    subgraph Database["💾 Supabase Database"]
        raw_story[("stories_raw<br/>סטוריז גולמיים")]
        relevant_story[("relevant_story<br/>קופונים + לינקים")]
        recommendations[("story_recommendations<br/>המלצות")]
        coupon_table[("relevant_story_coupon<br/>פירוט קופונים")]
    end

    subgraph Classification["🏷️ Classification Logic"]
        decision{{"AI Decision"}}
        coupon["Coupon<br/>יש קוד קופון"]
        collab["Collab Ad<br/>יש לינק מכירה"]
        recommend["Recommendation<br/>אזכור מותג בלבד"]
        organic["Organic<br/>לא רלוונטי"]
    end

    %% Flow connections
    influencers --> get_ids
    get_ids --> user_ids
    user_ids --> run_daily_stories
    run_daily_stories --> stories_php
    stories_php --> raw_story
    
    raw_story --> run_daily
    user_ids -.מיפוי שמות.-> run_daily
    run_daily --> openai
    openai --> decision
    
    decision -->|"is_relevant: true<br/>content_type: coupon"| coupon
    decision -->|"is_relevant: true<br/>content_type: collab_ad"| collab
    decision -->|"is_relevant: true<br/>content_type: recommendation"| recommend
    decision -->|"is_relevant: false<br/>content_type: organic"| organic
    
    coupon --> relevant_story
    collab --> relevant_story
    recommend --> recommendations
    organic -.מסונן.-> X[❌]
    
    relevant_story -.פירוט.-> coupon_table

    %% Styling
    classDef inputStyle fill:#e1f5ff,stroke:#0288d1,stroke-width:2px
    classDef processStyle fill:#fff3e0,stroke:#f57c00,stroke-width:2px
    classDef aiStyle fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    classDef dbStyle fill:#e8f5e9,stroke:#388e3c,stroke-width:2px
    classDef classStyle fill:#fff9c4,stroke:#f9a825,stroke-width:2px
    
    class influencers,user_ids inputStyle
    class get_ids,stories_php,run_daily_stories processStyle
    class run_daily,openai aiStyle
    class raw_story,relevant_story,recommendations,coupon_table dbStyle
    class decision,coupon,collab,recommend,organic classStyle
```

## Components Description

### Input Layer
- **influencers.txt**: List of influencer usernames to track
- **user_ids_*.json**: Mapping between Instagram user IDs and usernames

### Data Collection
- **get_user_ids.php**: Converts usernames to Instagram user IDs
- **stories_with_stickers.php**: Fetches stories and performs OCR
- **run_daily_stories.py**: Daily automation script

### AI Processing
- **run_daily.py**: Main processing and classification script
- **OpenAI GPT-4o-mini**: Vision + text analysis for content classification

### Database Tables
- **stories_raw**: Raw story data from Instagram
- **relevant_story**: Stories with coupons or affiliate links
- **story_recommendations**: Stories with brand mentions (no direct link/coupon)
- **relevant_story_coupon**: Detailed coupon information

### Classification Types
1. **Coupon**: Has a coupon code → saved to `relevant_story`
2. **Collab Ad**: Has affiliate/purchase link → saved to `relevant_story`
3. **Recommendation**: Brand mention only → saved to `story_recommendations`
4. **Organic**: No commercial intent → filtered out
