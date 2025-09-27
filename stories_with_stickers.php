<?php
// stories_with_stickers.php
// Usage: php stories_with_stickers.php <USER_ID>
// Env required: IG_SESSIONID, IG_CSRF, IG_DS_USER_ID
// Optional: IG_UA, TESSERACT_PATH (for OCR), FFMPEG_PATH (video frame OCR), OCR_LANGS (e.g. "heb+eng")
// Optional: IG_WWW_CLAIM, IG_DEBUG=1 (dump story_debug.json)
declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';

use GuzzleHttp\Client;
use GuzzleHttp\Handler\CurlHandler;
use GuzzleHttp\HandlerStack;

/* ---------- mbstring fallbacks ---------- */
function u_lower(string $s): string { return function_exists('mb_strtolower') ? mb_strtolower($s,'UTF-8') : strtolower($s); }
function u_len(string $s): int { return function_exists('mb_strlen') ? mb_strlen($s,'UTF-8') : strlen($s); }

/* ---------- helpers ---------- */
function envs(string $k, ?string $def=''): string { $v=getenv($k); return ($v===false)?(string)$def:(string)$v; }
function jdie(string $m,int $c=1): void { fwrite(STDERR,$m.PHP_EOL); exit($c); }
function firstNonEmpty(...$vals){ foreach($vals as $v){ if($v!==null && $v!=='') return $v; } return null; }
function isHebrew(string $s): bool { return (bool)preg_match('/\p{Hebrew}/u',$s); }
function langGuess(?string $t): ?string {
  if ($t===null || trim($t)==='') return null;
  if (isHebrew($t)) return 'he';
  $letters=preg_replace('/[^A-Za-z]/','',$t);
  if ($letters!=='' && strlen($letters)>=max(3,(int)(strlen($t)*0.2))) return 'en';
  return null;
}
function uniqStrings(array $arr): array {
  $seen=[]; $out=[];
  foreach($arr as $v){ $v=(string)$v; $k=u_lower(trim($v)); if($k!=='' && !isset($seen[$k])){ $seen[$k]=1; $out[]=$v; } }
  return array_values($out);
}
function collectHashtagsFromCaption(?string $c): array {
  if(!$c) return [];
  if(preg_match_all('/#([\p{L}\p{N}_]+)/u',$c,$m)) return uniqStrings($m[1]);
  return [];
}
function collectMentionsFromCaption(?string $c): array {
  if(!$c) return [];
  if(preg_match_all('/@([\p{L}\p{N}_.]+)/u',$c,$m)) return uniqStrings($m[1]);
  return [];
}
function resolveDomain(?string $url): ?string {
  if(!$url) return null;
  $h=parse_url($url,PHP_URL_HOST);
  return is_string($h)&&$h!==''?$h:null;
}
function toIso(?int $ts): ?string { if(!$ts) return null; return date('c',$ts); }
function bboxOrDefault(?array $src): array {
  if(!$src) return [0.0,0.0,0.0,0.0];
  $x=isset($src['x'])?(float)$src['x']:0.0; $y=isset($src['y'])?(float)$src['y']:0.0;
  $w=isset($src['width'])?(float)$src['width']:0.0; $h=isset($src['height'])?(float)$src['height']:0.0;
  return [$x,$y,$w,$h];
}
function stickerTextOf(?array $src): string {
  if(!$src) return '';
  $t = firstNonEmpty($src['title']??null,$src['text']??null,$src['name']??null,$src['question']??null);
  return (string)($t??'');
}
function classifySticker(?string $text, ?string $url=null): string {
  $t=u_lower($text??'');
  if($url) return 'url'; if($t==='') return 'generic';
  if (preg_match('/(?:^|[\s])(?:₪|\$|€)\s*\d+(?:[.,]\d+)?/u',$t) || preg_match('/\d+(?:[.,]\d+)?\s*(?:₪|ש"ח|\$|€)/u',$t)) return 'price';
  if (preg_match('/\b\d{1,3}\s?%\b/u',$t)) return 'percent';
  if (preg_match('/\b(coupon|קופון|promo|voucher)\b/u',$t)) return 'coupon';
  if (preg_match('/\b(20\d{2}|19\d{2})[-\/\.](0?[1-9]|1[0-2])[-\/\.](0?[1-9]|[12]\d|3[01])\b/u',$t)) return 'date';
  return 'generic';
}

/* ---------- deep URL harvester ---------- */
function harvestUrlsDeep(mixed $data, array &$out): void {
  if (is_string($data)) {
    // נכניס כל http/https (כולל l.instagram.com). רק נקזז קבצי מדיה CDN.
    if (preg_match('~^https?://~i',$data)) {
      $u=trim($data);
      $host=strtolower((string)(parse_url($u,PHP_URL_HOST)??''));
      $isCdn = preg_match('~(^|\.)(cdninstagram\.com|fbcdn\.net|fna\.fbcdn\.net)$~i',$host);
      $isMedia = preg_match('~\.(jpg|jpeg|png|webp|mp4|mov)(\?|$)~i',$u);
      if (!$isCdn || !$isMedia) {
        $out[strtolower($u)] = ['text'=>$u,'resolved_domain'=>resolveDomain($u)];
      }
    }
    return;
  }
  if (is_array($data)) {
    foreach ($data as $k=>$v) harvestUrlsDeep($v,$out);
  }
}

/* ---------- unwrap l.instagram.com shim ---------- */
function unwrapInstagramShim(string $url): string {
  $parts = parse_url($url);
  $host = strtolower($parts['host'] ?? '');
  if ($host === 'l.instagram.com') {
    parse_str($parts['query'] ?? '', $q);
    if (!empty($q['u'])) {
      $decoded = urldecode($q['u']);
      // הגנה בסיסית: אם יש עוד עיטופים – נסיר
      return $decoded;
    }
  }
  return $url;
}

/* ---------- coupon detector ---------- */
function couponCodesFromText(string $t): array {
  $out=[];
  if (preg_match_all('/(?:קופון|coupon|promo|voucher)\s*[:：]?\s*([A-Za-z0-9_-]{3,20})/iu', $t, $m)) {
    foreach($m[1] as $c){ $out[] = strtoupper($c); }
  }
  return array_values(array_unique($out));
}

/* ---------- download helper for OCR ---------- */
function downloadToTemp(Client $client, string $url, string $suffix): ?string {
  try {
    $res=$client->get($url,['http_errors'=>false,'stream'=>true,'timeout'=>30]);
    if($res->getStatusCode()!==200) return null;
    $tmp=tempnam(sys_get_temp_dir(),'ig_');
    $path=$tmp.$suffix;
    $fh=fopen($path,'wb');
    foreach($res->getBody() as $chunk){ fwrite($fh,$chunk); }
    fclose($fh);
    return $path;
  } catch (\Throwable $e) { return null; }
}

/* ---------- OCR (Tesseract) ---------- */
function tesseractAvailable(): bool {
  $bin=envs('TESSERACT_PATH', '');
  return $bin!=='' && is_file($bin);
}
function runTesseract(string $imagePath, string $langs='heb+eng'): ?string {
  $bin=envs('TESSERACT_PATH','');
  if($bin==='') return null;
  $outPath=$imagePath.'.out';
  $cmd='"'.$bin.'" '.escapeshellarg($imagePath).' '.escapeshellarg($outPath).' -l '.escapeshellarg($langs).' 2>NUL';
  exec($cmd, $o, $rc);
  if($rc!==0) return null;
  $txt=@file_get_contents($outPath.'.txt');
  return $txt!==false ? trim($txt) : null;
}

/* ---------- Extract URLs / emails / IG handles from free text ---------- */
function urlsFromText(string $text): array {
  $urls=[];
  // נרמול ידיות בסגנון "handle@" -> "@handle"
  $textNorm = preg_replace('/\b([A-Za-z0-9._]{2,30})@/u', '@$1', $text);

  // http(s) URLs
  if (preg_match_all('~https?://[^\s\)\]]+~iu',$textNorm,$m)){
    foreach($m[0] as $u){
      $u=trim($u);
      $urls[strtolower($u)]=['text'=>unwrapInstagramShim($u),'resolved_domain'=>resolveDomain($u)];
    }
  }
  // emails -> נמיר ל mailto: וגם נוסיף דומיין כרמז
  if (preg_match_all('/[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}/iu',$textNorm,$m)){
    foreach($m[0] as $e){
      $e=trim($e);
      $mailto='mailto:'.$e;
      $urls[strtolower($mailto)]=['text'=>$mailto,'resolved_domain'=>resolveDomain('http://'.substr($e,strpos($e,'@')+1))];
    }
  }
  // @handles -> נבנה כתובת IG פרופיל
  if (preg_match_all('/@([A-Za-z0-9._]{2,30})/u',$textNorm,$m)){
    foreach($m[1] as $h){
      $u='https://www.instagram.com/'.$h;
      $urls[strtolower($u)]=['text'=>$u,'resolved_domain'=>'www.instagram.com'];
    }
  }
  return array_values($urls);
}

/* ---------- inputs/env ---------- */
$uid = $argv[1] ?? null;
if (!$uid) jdie("Usage: php stories_with_stickers.php <USER_ID>",2);

$csrf=envs('IG_CSRF',null); $sess=envs('IG_SESSIONID',null); $dsid=envs('IG_DS_USER_ID',null);
$ua  =envs('IG_UA','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36');
if(!$csrf||!$sess||!$dsid) jdie("Missing env: IG_CSRF / IG_SESSIONID / IG_DS_USER_ID",3);

$handler=HandlerStack::create(new CurlHandler());
$verifyPath=ini_get('curl.cainfo'); if(!$verifyPath) $verifyPath=ini_get('openssl.cafile')?:true;
$client=new Client([
  'base_uri'=>'https://www.instagram.com/','handler'=>$handler,'http_errors'=>false,'timeout'=>30,
  'decode_content'=>true,'verify'=>$verifyPath,'force_ip_resolve'=>'v4','curl'=>[CURLOPT_HTTP_VERSION=>CURL_HTTP_VERSION_1_1],
  'headers'=>[
    'User-Agent'=>$ua,
    'Referer'=>'https://www.instagram.com/',
    'Origin'=>'https://www.instagram.com',
    'Accept'=>'*/*',
    'Accept-Language'=>'en-US,en;q=0.9',
    'Accept-Encoding'=>'gzip, deflate',
    'X-Requested-With'=>'XMLHttpRequest',
    'X-IG-App-ID'=>'936619743392459',
    'X-CSRFToken'=>$csrf,
    // אפשר להעשיר קוקיז אם יש לך (mid, ig_did, ig_nrcb, וכו')
    'Cookie'=>"csrftoken={$csrf}; sessionid={$sess}; ds_user_id={$dsid};",
    'Sec-Fetch-Site'=>'same-origin','Sec-Fetch-Mode'=>'cors','Sec-Fetch-Dest'=>'empty',
    // אופציונלי: 'X-IG-WWW-Claim'=>envs('IG_WWW_CLAIM','0'),
  ],
]);

/* ---------- API call ---------- */
$res=$client->post('api/v1/feed/reels_media/',['form_params'=>['user_ids'=>json_encode([(string)$uid],JSON_UNESCAPED_SLASHES)]]);
$code=$res->getStatusCode(); $body=(string)$res->getBody();
if($code!==200) jdie("HTTP {$code} response:\n{$body}",1);
$j=json_decode($body,true); if(!is_array($j)) jdie("Bad JSON\n{$body}",1);

/* ---------- transform ---------- */
$out=[]; $tray=$j['reels'][(string)$uid]['items'] ?? ($j['reels_media'][0]['items'] ?? ($j['items'] ?? []));
$ocrLangs = envs('OCR_LANGS','heb+eng');
$ffmpeg   = envs('FFMPEG_PATH','');
$debugOn  = envs('IG_DEBUG','')==='1';

foreach($tray as $it){
  $mediaId=$it['id']??null;
  $owner=$it['user']??[]; $userPk=firstNonEmpty($owner['pk']??null,$owner['pk_id']??null,$owner['id']??null);
  $username=$owner['username']??null;

  $width=$it['original_width']??0; $height=$it['original_height']??0; $durMs=null;
  if(isset($it['video_duration'])) $durMs=(int)round((float)$it['video_duration']*1000);

  $imageUrl=firstNonEmpty($it['image_versions2']['candidates'][0]['url']??null,$it['display_url']??null,$it['image_url']??null);
  $videoUrl=firstNonEmpty($it['video_versions'][0]['url']??null,$it['video_url']??null);

  $captionText=null;
  if(isset($it['caption'])){
    $captionText=is_array($it['caption'])?($it['caption']['text']??null):(string)$it['caption'];
    if($captionText!==null) $captionText=trim($captionText);
  }


  $takenAtIso=toIso($it['taken_at']??null); $expiringIso=toIso($it['expiring_at']??null);
  $type='story';

  /* ----- URLs from structured fields + deep scan ----- */
  $urls=[];
  $procErrors=[];

  // 1) story_cta
  if(!empty($it['story_cta'])) foreach($it['story_cta'] as $cta){
    foreach(($cta['links']??[]) as $lnk){
      $u = $lnk['webUri'] ?? $lnk['url'] ?? $lnk['link_url']
         ?? (($lnk['story_link'] ?? [])['link_context']['url'] ?? null);
      if($u){
        $un=unwrapInstagramShim($u);
        $urls[]=['text'=>trim($un),'resolved_domain'=>resolveDomain($un)];
      }
    }
  }

  // 2) story_link_stickers
  if(!empty($it['story_link_stickers'])) foreach($it['story_link_stickers'] as $ls){
    $u = $ls['story_link']['url'] ??
         $ls['url'] ??
         $ls['link_url'] ??
         (($ls['story_link'] ?? [])['link_context']['url'] ?? null);
    if($u){
      $un=unwrapInstagramShim($u);
      $urls[]=['text'=>trim($un),'resolved_domain'=>resolveDomain($un)];
    }
  }

  // 3) tappable_objects (object_type=link + other link types)
  if(!empty($it['tappable_objects'])) foreach($it['tappable_objects'] as $to){
    $objType = $to['object_type'] ?? '';
    if ($objType === 'link'){
      $u=$to['link']['url']??($to['url']??null);
      if($u){
        $un=unwrapInstagramShim($u);
        $urls[]=['text'=>trim($un),'resolved_domain'=>resolveDomain($un)];
      }
    }
    // Additional object types that may contain shopping/store links
    elseif (in_array($objType, ['product', 'shopping', 'storefront', 'external_link', 'web_link'])) {
      $u = $to['product']['external_url'] ??
           $to['shopping']['url'] ??
           $to['storefront']['url'] ??
           $to['external_link']['url'] ??
           $to['web_link']['url'] ??
           $to['url'] ?? null;
      if($u){
        $un=unwrapInstagramShim($u);
        $urls[]=['text'=>trim($un),'resolved_domain'=>resolveDomain($un)];
      }
    }
  }

  // 4) story_bloks_stickers – לעיתים URL ב-bloks_data
  if(!empty($it['story_bloks_stickers'])) foreach($it['story_bloks_stickers'] as $bl){
    $data=$bl['bloks_sticker']['bloks_data']??($bl['bloks_data']??null);
    if(is_string($data)) $data=json_decode($data,true);
    $map=[];
    if($data) harvestUrlsDeep($data,$map);
    $u = $bl['url'] ?? ($bl['link_url'] ?? null);
    if($u){
      $un=unwrapInstagramShim($u);
      $map[strtolower($un)]=['text'=>trim($un),'resolved_domain'=>resolveDomain($un)];
    }
    foreach($map as $v){ $urls[]=$v; }
  }

  // 5) story_app_attribution
  if(!empty($it['story_app_attribution'])) foreach($it['story_app_attribution'] as $app){
    $u=$app['url']??($app['link']??($app['app_action_url']??null));
    if($u){
      $un=unwrapInstagramShim($u);
      $urls[]=['text'=>trim($un),'resolved_domain'=>resolveDomain($un)];
    }
  }

  // 6) story_shopping_stickers (shopping tags)
  if(!empty($it['story_shopping_stickers'])) foreach($it['story_shopping_stickers'] as $shop){
    $u = $shop['shopping_sticker']['url'] ??
         $shop['url'] ??
         $shop['external_url'] ?? null;
    if($u){
      $un=unwrapInstagramShim($u);
      $urls[]=['text'=>trim($un),'resolved_domain'=>resolveDomain($un)];
    }
  }

  // 7) story_cta_stickers (call-to-action stickers)
  if(!empty($it['story_cta_stickers'])) foreach($it['story_cta_stickers'] as $cta){
    $u = $cta['cta_sticker']['url'] ??
         $cta['url'] ??
         $cta['action_url'] ?? null;
    if($u){
      $un=unwrapInstagramShim($u);
      $urls[]=['text'=>trim($un),'resolved_domain'=>resolveDomain($un)];
    }
  }

  // 8) Check for swipe_up_link or action_link (common in influencer content)
  if(!empty($it['swipe_up_link'])){
    $u = is_array($it['swipe_up_link']) ?
         ($it['swipe_up_link']['url'] ?? $it['swipe_up_link']['link_url'] ?? null) :
         $it['swipe_up_link'];
    if($u){
      $un=unwrapInstagramShim($u);
      $urls[]=['text'=>trim($un),'resolved_domain'=>resolveDomain($un)];
    }
  }

  if(!empty($it['action_link'])){
    $u = is_array($it['action_link']) ?
         ($it['action_link']['url'] ?? $it['action_link']['link_url'] ?? null) :
         $it['action_link'];
    if($u){
      $un=unwrapInstagramShim($u);
      $urls[]=['text'=>trim($un),'resolved_domain'=>resolveDomain($un)];
    }
  }

  // deep harvest on other sticker containers
  foreach (['story_product_items','story_feed_media','story_music_stickers','story_shopping_stickers','story_cta_stickers'] as $k){
    if(!empty($it[$k])){
      $map=[]; harvestUrlsDeep($it[$k],$map);
      foreach($map as $v){ $urls[]=$v; }
    }
  }

  if($captionText){
    foreach(urlsFromText($captionText) as $u){ $urls[strtolower($u['text'])]=$u; }
  }

  // uniq by text
  $urls = array_values(array_reduce($urls,function($acc,$it){
    $acc[strtolower($it['text'])]=$it; return $acc;
  },[]));

  /* ----- hashtags & mentions ----- */
  $hashtags=[]; if(!empty($it['story_hashtags'])) foreach($it['story_hashtags'] as $h){ $n=$h['hashtag']['name']??null; if($n) $hashtags[]=$n; }
  $hashtags=uniqStrings(array_merge($hashtags,collectHashtagsFromCaption($captionText)));
  $mentions=[]; if(!empty($it['reel_mentions'])) foreach($it['reel_mentions'] as $m){ $u=$m['user']['username']??null; if($u) $mentions[]=$u; }
  if(!empty($it['tappable_objects'])) foreach($it['tappable_objects'] as $to){
    if(($to['object_type']??'')==='mention'){
      $u=$to['user']['username']??($to['username']??null); if($u) $mentions[]=$u;
    }
  }
  $mentions=uniqStrings(array_merge($mentions,collectMentionsFromCaption($captionText)));

  /* ----- stickers (structured) ----- */
  $stickers=[];
  if(!empty($it['story_cta'])) foreach($it['story_cta'] as $cta){ foreach(($cta['links']??[]) as $lnk){
    $u=$lnk['webUri']??($lnk['url']??($lnk['link_url']??(($lnk['story_link']['link_context']['url']??null))));
    $un=$u?unwrapInstagramShim($u):null;
    $text=stickerTextOf($lnk);
    if($un||$text){
      $stickers[]=['type'=>classifySticker($text,$un),'text'=>$text?:($un??''),'bbox'=>[0,0,0,0],'confidence'=>0.0];
    }
  }}
  if(!empty($it['story_link_stickers'])) foreach($it['story_link_stickers'] as $ls){
    $u=$ls['story_link']['url']??
       ($ls['url']??
       ($ls['link_url']??
       (($ls['story_link']['link_context']['url']??null))));
    $un=$u?unwrapInstagramShim($u):null;
    $text=stickerTextOf($ls);
    $stickers[]=['type'=>classifySticker($text,$un),'text'=>$text?:($un??''),'bbox'=>bboxOrDefault($ls),'confidence'=>0.0];
  }
  if(!empty($it['tappable_objects'])) foreach($it['tappable_objects'] as $to){
    $typeObj=$to['object_type']??''; $text=stickerTextOf($to);
    $u=$to['link']['url']??($to['url']??null); $un=$u?unwrapInstagramShim($u):null;
    if($typeObj==='link'||$un){ $stickers[]=['type'=>'url','text'=>$un?:$text,'bbox'=>bboxOrDefault($to),'confidence'=>0.0]; }
    elseif($text!==''){ $stickers[]=['type'=>classifySticker($text,null),'text'=>$text,'bbox'=>bboxOrDefault($to),'confidence'=>0.0]; }
  }
  foreach (($it['story_polls']??[]) as $p){
    $s=$p['poll_sticker']??[]; $text=trim(($s['question']??'').' '.implode(' ',array_map(fn($t)=>$t['text']??'',$s['tallies']??[])));
    if($text!=='') $stickers[]=['type'=>'generic','text'=>$text,'bbox'=>bboxOrDefault($s),'confidence'=>0.0];
  }
  foreach (($it['story_sliders']??[]) as $s){
    $st=$s['slider_sticker']??[]; $text=trim(($st['question']??'').' '.($st['emoji']??'')); if($text!=='') $stickers[]=['type'=>'generic','text'=>$text,'bbox'=>bboxOrDefault($st),'confidence'=>0.0];
  }
  $quizArr=$it['story_quizs']??($it['story_quiz']??[]);
  foreach($quizArr as $q){
    $st=$q['quiz_sticker']??[]; $choices=array_map(fn($t)=>$t['text']??'',$st['tallies']??[]); $text=trim(($st['question']??'').' '.implode(' ',$choices));
    if($text!=='') $stickers[]=['type'=>'generic','text'=>$text,'bbox'=>bboxOrDefault($st),'confidence'=>0.0];
  }
  foreach (($it['story_questions']??[]) as $q){
    $st=$q['question_sticker']??[]; $text=$st['question']??($st['question_text']??''); if($text!=='') $stickers[]=['type'=>'generic','text'=>$text,'bbox'=>bboxOrDefault($st),'confidence'=>0.0];
  }

  // story_text_stickers - Instagram's built-in text stickers (typed text)
  if(!empty($it['story_static_models'])) foreach($it['story_static_models'] as $sm){
    $text = $sm['text']??($sm['display_text']??($sm['sticker_text']??''));
    if($text!=='') {
      $stickers[]=['type'=>'generic','text'=>$text,'bbox'=>bboxOrDefault($sm),'confidence'=>1.0];
      $rawTextCandidates[]=$text;
      // Check for coupon codes in text
      foreach(couponCodesFromText($text) as $c){
        $stickers[]=['type'=>'coupon','text'=>$c,'bbox'=>bboxOrDefault($sm),'confidence'=>1.0];
      }
    }
  }

  // story_overlay_stickers - overlay text elements
  if(!empty($it['story_overlay_stickers'])) foreach($it['story_overlay_stickers'] as $os){
    $text = $os['text']??($os['display_text']??($os['sticker_text']??''));
    if($text!=='') {
      $stickers[]=['type'=>'generic','text'=>$text,'bbox'=>bboxOrDefault($os),'confidence'=>1.0];
      $rawTextCandidates[]=$text;
      foreach(couponCodesFromText($text) as $c){
        $stickers[]=['type'=>'coupon','text'=>$c,'bbox'=>bboxOrDefault($os),'confidence'=>1.0];
      }
    }
  }

  /* ----- optional debug dump of raw story fields ----- */
  if ($debugOn) {
    $debug = [];
    foreach (['story_cta','story_link_stickers','tappable_objects','story_bloks_stickers','story_app_attribution','story_shopping_stickers','story_cta_stickers','swipe_up_link','action_link','story_static_models','story_text_stickers','story_overlay_stickers'] as $k) {
      if (!empty($it[$k])) $debug[$k] = $it[$k];
    }
    // Also capture any field that might contain text or ALL fields for complete debugging
    if (envs('IG_DEBUG_FULL', '') === '1') {
      // Full debug - dump everything
      $debug = $it;
    } else {
      // Selective debug - only text/sticker related fields
      foreach (array_keys($it) as $k) {
        if (strpos($k, 'text') !== false || strpos($k, 'sticker') !== false || strpos($k, 'static') !== false || strpos($k, 'overlay') !== false) {
          if (!empty($it[$k]) && !isset($debug[$k])) {
            $debug[$k] = $it[$k];
          }
        }
      }
    }
    if (!empty($debug)) {
      @file_put_contents(__DIR__.'/story_debug.json', json_encode($debug, JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT));
    }
  }

  /* ----- OCR (image/video) ----- */
  $rawTextCandidates=[]; if($captionText) $rawTextCandidates[]=$captionText;
  $hasText=false;

  // Extract text from Instagram's accessibility_caption (AI-generated text description)
  if(isset($it['accessibility_caption'])){
    $accessibilityText = trim((string)$it['accessibility_caption']);
    // Extract text after "טקסט שאומר" (Hebrew) or "text that says" (English)
    if(preg_match('/טקסט שאומר\s+[\'"]([^\'"]++)[\'"]/', $accessibilityText, $matches) ||
       preg_match('/text that says\s+[\'"]([^\'"]++)[\'"]/', $accessibilityText, $matches)){
      $extractedText = trim($matches[1]);
      if($extractedText !== ''){
        $rawTextCandidates[] = $extractedText;
        $hasText = true;
        // Check for coupon codes
        foreach(couponCodesFromText($extractedText) as $c){
          $stickers[]=['type'=>'coupon','text'=>$c,'bbox'=>[0,0,0,0],'confidence'=>0.9];
        }
        // Add as generic text sticker
        $stickers[]=['type'=>'generic','text'=>$extractedText,'bbox'=>[0,0,0,0],'confidence'=>0.9];
        // Check for URLs in extracted text
        foreach(urlsFromText($extractedText) as $u){ $urls[strtolower($u['text'])]=$u; }
      }
    }
  }

  if (tesseractAvailable()) {
    // image story
    if ($imageUrl && (!$videoUrl || ($durMs??0)===0)) {
      $imgPath = downloadToTemp($client,$imageUrl,'.jpg');
      if ($imgPath){
        $txt=runTesseract($imgPath,$ocrLangs);
        if ($txt && trim($txt)!==''){
          $rawTextCandidates[]=$txt;
          $hasText=true;
          foreach(urlsFromText($txt) as $u){ $urls[strtolower($u['text'])]=$u; }
          // קופונים
          foreach(couponCodesFromText($txt) as $c){
            $stickers[]=['type'=>'coupon','text'=>$c,'bbox'=>[0,0,0,0],'confidence'=>0.0];
          }
        }
      }
    }
    // video story -> prefer ffmpeg; fallback to thumbnail if missing/failed
    elseif ($videoUrl) {
      $frameTxt = null;
      $ffOk = false;

      if ($ffmpeg!=='') {
        $vid = downloadToTemp($client,$videoUrl,'.mp4');
        if ($vid){
          $frame = $vid.'.jpg';
          $sec = max(1, min( floor((($durMs??60000)/1000)/2), 45 ));
          $cmd = '"'.$ffmpeg.'" -y -ss '.$sec.' -i '.escapeshellarg($vid).' -frames:v 1 '.escapeshellarg($frame).' 2>NUL';
          exec($cmd,$o,$rc);
          if ($rc===0 && is_file($frame)){
            $frameTxt=runTesseract($frame,$ocrLangs);
            $ffOk = true;
          } else {
            $procErrors[] = 'ffmpeg_extract_failed';
          }
        } else {
          $procErrors[] = 'ffmpeg_download_failed';
        }
      } else {
        $procErrors[] = 'ffmpeg_missing';
      }

      if ($frameTxt && trim($frameTxt)!=='') {
        $rawTextCandidates[]=$frameTxt; $hasText=true;
        foreach(urlsFromText($frameTxt) as $u){ $urls[strtolower($u['text'])]=$u; }
        foreach(couponCodesFromText($frameTxt) as $c){
          $stickers[]=['type'=>'coupon','text'=>$c,'bbox'=>[0,0,0,0],'confidence'=>0.0];
        }
      } else {
        // Fallback OCR על ה-thumbnail
        if ($imageUrl){
          $imgPath = downloadToTemp($client,$imageUrl,'.jpg');
          if ($imgPath){
            $txt=runTesseract($imgPath,$ocrLangs);
            if ($txt && trim($txt)!==''){
              $rawTextCandidates[]=$txt; $hasText=true;
              foreach(urlsFromText($txt) as $u){ $urls[strtolower($u['text'])]=$u; }
              foreach(couponCodesFromText($txt) as $c){
                $stickers[]=['type'=>'coupon','text'=>$c,'bbox'=>[0,0,0,0],'confidence'=>0.0];
              }
            }
          }
        }
      }
    }
  }

  // dedupe raw texts
  $rawTextCandidates = uniqStrings(array_merge($rawTextCandidates, array_values(array_filter(array_map(fn($s)=>$s['text']??'', $stickers)))));

  // add URL stickers for found urls (if missing)
  $haveUrlSticker=[]; foreach($stickers as $s){ if(($s['type']??'')==='url' && !empty($s['text'])) $haveUrlSticker[strtolower($s['text'])]=1; }
  foreach($urls as $u){
    $k=strtolower($u['text']);
    if(empty($haveUrlSticker[$k])) $stickers[]=['type'=>'url','text'=>$u['text'],'bbox'=>[0,0,0,0],'confidence'=>0.0];
  }

  // frames_used heuristic
  $framesUsed=[]; if($durMs && $durMs>0){ $framesUsed=[0, min(45000,max(0,$durMs-1)), min(90000,max(0,$durMs-1))]; $framesUsed=array_values(array_unique($framesUsed)); }

  // OCR fields in schema
  $ocrText = null; $ocrConf = 0.0; // (אפשר לשדרג ל-hOCR/TSV להפקת confidence אמיתי)
  // flags
  $hasText = $hasText || ($captionText!==null && $captionText!=='') || !empty($rawTextCandidates);

  // language guess
  $lang = langGuess($captionText ?? ($rawTextCandidates[0] ?? null));

  // content hash
  $hashBase=json_encode(['media_id'=>$mediaId,'caption'=>$captionText,'urls'=>array_map(fn($u)=>$u['text'],$urls),'hashtags'=>$hashtags,'mentions'=>$mentions],JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
  $contentHash=sha1((string)$hashBase);

  // processing errors compose
  $procErr = tesseractAvailable() ? [] : ['ocr_not_enabled'];
  $procErr = array_values(array_unique(array_merge($procErr,$procErrors)));

  $obj=[
    "media_id"        => (string)($mediaId??''),
    "user_id"         => $userPk ? (string)$userPk : (string)$uid,
    "username"        => $username ?? null,
    "type"            => $type,
    "taken_at_iso"    => $takenAtIso,
    "expiring_at_iso" => $expiringIso,

    "permalink"       => null,
    "image_url"       => $imageUrl ?? null,
    "video_url"       => $videoUrl ?? null,

    "caption_text"    => $captionText ?? null,
    "ocr_text"        => $ocrText,
    "ocr_confidence"  => (float)$ocrConf,

    "stickers"        => $stickers,
    "urls"            => array_values($urls),
    "raw_text_candidates" => $rawTextCandidates,
    "hashtags"        => $hashtags,
    "mentions"        => $mentions,

    "frames_used"     => $framesUsed,
    "media_meta"      => ["width"=>(int)$width,"height"=>(int)$height,"duration_ms"=>(int)($durMs??0)],

    "language_guess"  => $lang,
    "brand_candidates"=> [],
    "source_flags"    => ["has_text"=>(bool)$hasText,"has_stickers"=>!empty($stickers),"has_logo_hint"=>false],

    "content_hash"    => $contentHash,
    "processing"      => ["extraction_version"=>"1.1.0","errors"=> $procErr],
  ];
  $out[]=$obj;
}

/* ---------- output ---------- */
header('Content-Type: application/json; charset=utf-8');
echo json_encode($out, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT), PHP_EOL;
