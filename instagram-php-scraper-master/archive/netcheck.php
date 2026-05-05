<?php
require __DIR__ . '/vendor/autoload.php';
use GuzzleHttp\Client;

$client = new Client(['timeout' => 10, 'verify' => 'C:\php\extras\ssl\cacert.pem']);

foreach (['https://example.com','https://www.google.com'] as $url) {
  try {
    $r = $client->get($url);
    echo $url.' => '.$r->getStatusCode()."\n";
  } catch (\Throwable $e) {
    echo $url.' => ERROR: '.$e->getMessage()."\n";
  }
}
