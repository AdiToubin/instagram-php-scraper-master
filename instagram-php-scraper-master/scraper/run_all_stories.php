<?php
// run_all_stories.php
// קורא את influencers.txt + קובץ ה-JSON העדכני של user_ids
// ומריץ את stories_with_stickers.php לכל משפיעניות
declare(strict_types=1);

function jdie(string $m, int $c=1): void { fwrite(STDERR, $m.PHP_EOL); exit($c); }

$baseDir         = dirname(__DIR__);
$influencersFile = $baseDir . '/influencers.txt';
$scraperDir      = __DIR__;
chdir($scraperDir); // מבטיח שה-exec() ירוץ מתיקיית scraper (נתיב יחסי בלי עברית)

// טעינת .env לתוך משתני הסביבה של תהליך זה (יורשים לתהליכי ילד)
$envFile = $baseDir . '/.env';
if (is_file($envFile)) {
    foreach (file($envFile, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
        $line = trim($line);
        if ($line === '' || $line[0] === '#') continue;
        if (preg_match('/^([^=]+)=(.*)$/', $line, $m)) {
            putenv(trim($m[1]) . '=' . trim($m[2]));
        }
    }
}

// קובץ ה-JSON העדכני ביותר
$jsonFiles = glob($baseDir . '/user_ids_*.json');
if (empty($jsonFiles)) jdie("לא נמצא קובץ user_ids_*.json ב-{$baseDir}");
usort($jsonFiles, fn($a,$b) => filemtime($b) - filemtime($a));
$jsonFile = $jsonFiles[0];

// טעינת מיפוי username -> user_id מה-JSON
$jsonData = json_decode(file_get_contents($jsonFile), true);
if (!is_array($jsonData)) jdie("קובץ JSON לא תקין: {$jsonFile}");
$uidMap = [];
foreach ($jsonData['results'] ?? [] as $r) {
    if (!empty($r['username']) && !empty($r['user_id'])) {
        $uidMap[$r['username']] = $r['user_id'];
    }
}

// קריאת influencers.txt
$lines = file($influencersFile, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
if ($lines === false) jdie("לא ניתן לקרוא: {$influencersFile}");

$userIds = [];
$notFound = [];
foreach ($lines as $line) {
    $line = trim($line);
    if ($line === '' || $line[0] === '#') continue;
    if (isset($uidMap[$line])) {
        $userIds[$line] = $uidMap[$line];
    } else {
        $notFound[] = $line;
    }
}

if (!empty($notFound)) {
    fwrite(STDERR, "WARN: לא נמצא user_id עבור: " . implode(', ', $notFound) . "\n");
    fwrite(STDERR, "הרץ תחילה את get_user_ids.php כדי לעדכן את ה-JSON\n\n");
}

if (empty($userIds)) jdie("אין user IDs להרצה");

echo "משתמשות ב-JSON: {$jsonFile}\n";
echo "מריץ סטורי עבור " . count($userIds) . " משפיעניות...\n\n";

// הרצה לכל משפיענית
$allStories = [];
$errors = [];
$totalStories = 0;

foreach ($userIds as $username => $userId) {
    echo "[@{$username}] user_id={$userId} ... ";
    flush();

    $cmd = 'php stories_with_stickers.php ' . escapeshellarg($userId) . ' 2>NUL';

    $stories = null;
    $rc = 0;
    for ($attempt = 1; $attempt <= 3; $attempt++) {
        $output = [];
        exec($cmd, $output, $rc);
        $stories = json_decode(implode("\n", $output), true);
        if ($rc === 0 && is_array($stories)) break;
        if ($attempt < 3) { echo "(retry {$attempt}) "; sleep(5); }
    }

    if ($rc !== 0 || !is_array($stories)) {
        echo "ERROR (exit {$rc})\n";
        $errors[] = ['username' => $username, 'user_id' => $userId,
                     'error' => is_array($stories) ? "exit code {$rc}" : 'invalid JSON'];
        sleep(1);
        continue;
    }

    $count = count($stories);
    $totalStories += $count;
    echo "✓ {$count} סטוריז\n";
    $allStories = array_merge($allStories, $stories);

    sleep(2);
}

// סיכום
echo "\n" . str_repeat("=", 60) . "\n";
echo "סיכום: " . count($userIds) . " משפיעניות | {$totalStories} סטוריז | " . count($errors) . " שגיאות\n";
echo str_repeat("=", 60) . "\n\n";

if (!empty($allStories)) {
    $outFile = $scraperDir . '/batch_stories_' . date('Y-m-d_His') . '.json';
    file_put_contents($outFile, json_encode([
        'timestamp'     => date('c'),
        'json_source'   => basename($jsonFile),
        'total_users'   => count($userIds),
        'total_stories' => $totalStories,
        'total_errors'  => count($errors),
        'all_stories'   => $allStories,
        'errors'        => $errors,
    ], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT));
    echo "✓ תוצאות נשמרו: {$outFile}\n";
}

if (!empty($errors)) {
    echo "\nשגיאות:\n";
    foreach ($errors as $e) {
        echo "  • @{$e['username']}: {$e['error']}\n";
    }
}
