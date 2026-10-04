#!/usr/bin/env bash
set -Eeuo pipefail

roundcube_root="${AXIORHUB_ROUNDCUBE_ROOT:-$(ls -d /var/www/html/roundcube* 2>/dev/null | sort -r | head -n1)}"
plugin="${roundcube_root:-/var/www/html/roundcube}/plugins/ai_roundcube_assistant"
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
stamp="$(date +%Y%m%d-%H%M%S)"
backup="/root/ai-roundcube-assistant-before-v8-${stamp}"

if [[ ${EUID} -ne 0 ]]; then
    echo "STOP : exécutez cet installateur avec sudo." >&2
    exit 1
fi
if [[ ! -d "$plugin" || ! -f "$plugin/ai_roundcube_assistant.php" ]]; then
    echo "STOP : plugin Roundcube introuvable : $plugin" >&2
    exit 1
fi

required=(
    ai_roundcube_assistant.php
    ai_roundcube_assistant.js
    ai_roundcube_assistant.css
    check-v8.mjs
    migrations/001_v8_postgresql.sql
    tests/002_v8_postgresql_contract.sql
)
for file in "${required[@]}"; do
    if [[ ! -s "$source_dir/$file" ]]; then
        echo "STOP : fichier V8 absent : $file" >&2
        exit 1
    fi
done

php_command=""
if command -v php8.4 >/dev/null 2>&1; then
    php_command="php8.4"
elif command -v php >/dev/null 2>&1; then
    php_command="php"
else
    echo "STOP : interpréteur PHP absent." >&2
    exit 1
fi
if ! "$php_command" -m | grep -qx 'pdo_pgsql'; then
    echo "STOP : l’extension PHP pdo_pgsql est absente." >&2
    exit 1
fi
for extension in curl dom fileinfo json mbstring openssl zip; do
    if ! "$php_command" -m | grep -qi "^${extension}$"; then
        echo "STOP : l’extension PHP ${extension} est absente." >&2
        exit 1
    fi
done

config_file="$plugin/config.inc.php"
if [[ ! -r "$config_file" ]]; then
    echo "STOP : configuration du plugin absente : $config_file" >&2
    exit 1
fi
for marker in ai_v8_pg_dsn ai_v8_pg_user ai_v8_pg_password_file ai_v8_signing_key_file ai_nextcloud_allowed_roots; do
    if ! grep -q "\['${marker}'\]" "$config_file"; then
        echo "STOP : paramètre V8 absent de config.inc.php : $marker" >&2
        exit 1
    fi
done

"$php_command" -l "$source_dir/ai_roundcube_assistant.php"
if command -v node >/dev/null 2>&1; then
    node --check "$source_dir/ai_roundcube_assistant.js"
    node "$source_dir/check-v8.mjs" "$source_dir"
else
    echo "STOP : Node.js est requis pour les contrôles V8." >&2
    exit 1
fi

echo "Contrôle de la configuration PostgreSQL et application de la migration V8…"
"$php_command" -r '
    $config = [];
    require $argv[1];
    $dsn = trim((string)($config["ai_v8_pg_dsn"] ?? ""));
    $user = trim((string)($config["ai_v8_pg_user"] ?? ""));
    $secretFile = trim((string)($config["ai_v8_pg_password_file"] ?? ""));
    if (!str_starts_with($dsn, "pgsql:") || $user === "" || !is_readable($secretFile)) {
        fwrite(STDERR, "Configuration PostgreSQL V8 incomplète.\n"); exit(2);
    }
    $perms = fileperms($secretFile);
    if ($perms === false || (($perms & 0007) !== 0) || (($perms & 0022) !== 0)) {
        fwrite(STDERR, "Permissions trop larges pour le secret PostgreSQL.\n"); exit(2);
    }
    $password = trim((string)file_get_contents($secretFile));
    $pdo = new PDO($dsn, $user, $password, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
    $sql = file_get_contents($argv[2]);
    if ($sql === false) { fwrite(STDERR, "Migration illisible.\n"); exit(2); }
    $pdo->exec($sql);
    foreach (["ai_integration_actions", "ai_audit_events", "ai_project_activities", "ai_calendar_event_links", "ai_file_import_fingerprints", "ai_project_snapshots"] as $table) {
        $pdo->query("SELECT 1 FROM " . $table . " LIMIT 1");
    }
' "$config_file" "$source_dir/migrations/001_v8_postgresql.sql"

echo "Exécution des contrats PostgreSQL V8 dans une transaction annulée…"
"$php_command" -r '
    $config = [];
    require $argv[1];
    $password = trim((string)file_get_contents((string)$config["ai_v8_pg_password_file"]));
    $pdo = new PDO((string)$config["ai_v8_pg_dsn"], (string)$config["ai_v8_pg_user"], $password, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
    $sql = file_get_contents($argv[2]);
    if ($sql === false) { fwrite(STDERR, "Test PostgreSQL illisible.\n"); exit(2); }
    $pdo->exec($sql);
' "$config_file" "$source_dir/tests/002_v8_postgresql_contract.sql"

signing_file="$($php_command -r '$config=[]; require $argv[1]; echo (string)($config["ai_v8_signing_key_file"] ?? "");' "$config_file")"
if [[ -z "$signing_file" || ! -r "$signing_file" ]]; then
    echo "STOP : clé de signature V8 absente ou illisible : $signing_file" >&2
    exit 1
fi
if [[ $(stat -c '%a' "$signing_file") != "640" && $(stat -c '%a' "$signing_file") != "600" ]]; then
    echo "STOP : la clé de signature doit être en mode 0640 ou 0600." >&2
    exit 1
fi
if [[ $(wc -c < "$signing_file") -lt 32 ]]; then
    echo "STOP : la clé de signature contient moins de 32 octets." >&2
    exit 1
fi

owner_uid="$(stat -c '%u' "$plugin/ai_roundcube_assistant.php")"
owner_gid="$(stat -c '%g' "$plugin/ai_roundcube_assistant.php")"
cp -a -- "$plugin" "$backup"
chmod 700 "$backup"

rollback() {
    echo "ERREUR : restauration du plugin depuis $backup" >&2
    cp -a -- "$backup/." "$plugin/"
    echo "La migration PostgreSQL additive est conservée ; elle ne supprime aucune donnée." >&2
}
trap rollback ERR

install -o "$owner_uid" -g "$owner_gid" -m 0644 \
    "$source_dir/ai_roundcube_assistant.php" "$plugin/ai_roundcube_assistant.php"
install -o "$owner_uid" -g "$owner_gid" -m 0644 \
    "$source_dir/ai_roundcube_assistant.js" "$plugin/ai_roundcube_assistant.js"
install -o "$owner_uid" -g "$owner_gid" -m 0644 \
    "$source_dir/ai_roundcube_assistant.css" "$plugin/ai_roundcube_assistant.css"
install -d -o "$owner_uid" -g "$owner_gid" -m 0755 "$plugin/migrations"
install -o "$owner_uid" -g "$owner_gid" -m 0644 \
    "$source_dir/migrations/001_v8_postgresql.sql" "$plugin/migrations/001_v8_postgresql.sql"

"$php_command" -l "$plugin/ai_roundcube_assistant.php"
node --check "$plugin/ai_roundcube_assistant.js"
node "$source_dir/check-v8.mjs" "$plugin"

trap - ERR
echo
echo "INSTALLATION V8 RÉUSSIE"
echo "Sauvegarde du plugin : $backup"
echo "Migration PostgreSQL : appliquée et contrôlée"
echo "Configuration et secrets : non modifiés"
echo
echo "Rechargez Roundcube sans cache puis exécutez les tests de réception de README-V8.txt."
