<?php
require __DIR__ . '/vendor/autoload.php';

use GuzzleHttp\Client;

$username = $argv[1] ?? 'instagram';

$client = new Client([
    'headers' => [
        'User-Agent' => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept' => 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language' => 'en-US,en;q=0.9',
    ],
    'timeout' => 30,
]);

try {
    $response = $client->get("https://www.instagram.com/{$username}/");
    $html = $response->getBody()->getContents();

    // Extract user ID from the HTML
    if (preg_match('/profilePage_(\d+)/', $html, $matches)) {
        echo $matches[1] . PHP_EOL;
    } elseif (preg_match('/"id":"(\d+)"/', $html, $matches)) {
        echo $matches[1] . PHP_EOL;
    } elseif (preg_match('/"user":{"id":"(\d+)"/', $html, $matches)) {
        echo $matches[1] . PHP_EOL;
    } else {
        echo "User ID not found" . PHP_EOL;
        exit(1);
    }

} catch (\Throwable $e) {
    echo "Error: " . $e->getMessage() . PHP_EOL;
    exit(1);
}