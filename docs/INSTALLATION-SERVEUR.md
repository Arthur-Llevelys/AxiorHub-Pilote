# Installation système (sans Docker) et mise à jour

Ce mode installe AxiorHub Pilote comme service système sur un serveur Ubuntu/Debian qui héberge déjà Nextcloud, Roundcube et
Apache. Pour un serveur neuf, préférez le [VPS clé en main](INSTALLATION-VPS.md).

## 1. Construire l'archive vérifiée

Depuis une copie du dépôt :

```bash
python3 scripts/build-release.py /tmp/axiorhub-mail-agent-5.6.6.tar.gz
```

L'archive contient un manifeste `MANIFEST.sha256` ; l'installateur refuse toute archive modifiée.

## 2. Installer ou mettre à jour

```bash
cd /tmp && tar -xzf axiorhub-mail-agent-5.6.6.tar.gz && cd axiorhub-mail-agent-5.6.6
sudo bash install.sh
```

- **Serveur neuf** : le code est copié dans `/opt/axiorhub-mail-agent/releases/5.6.6`, la configuration dans
  `/etc/axiorhub-mail-agent/` (secrets en 600), l'état dans `/var/lib/axiorhub-mail-agent/`, et les services systemd
  `axiorhub-mail-*` sont installés.
- **Mise à jour** (depuis 3.5.0 ou plus récent, sans passer par les versions intermédiaires) : les fichiers sont
  vérifiés, les bases migrées, le lien `current` basculé ; en cas d'échec, l'ancienne version reste active.
  Retour arrière : `sudo python3 upgrade.py --rollback`.

Une recette automatique de la version installée s'exécute ensuite en arrière-plan ; son résultat apparaît dans
*Pourquoi rien n'est produit ?*.

## 3. Configurer

```bash
sudo axiorhub-mail configure     # messagerie, Nextcloud, agendas, IA locale (saisie masquée des mots de passe)
sudo axiorhub-mail doctor        # contrôle des accès IMAP, Nextcloud, CalDAV et du modèle
```

Interface web sur le nom HTTPS existant :

```bash
sudo python3 /opt/axiorhub-mail-agent/current/install-interface.py
```

L'installateur d'interface ajoute une configuration Apache limitée au chemin `/agent-courriel/` et démarre les
services (interface, deux workers, surveillance IMAP). L'emplacement de Roundcube est détecté (`/var/www/html/roundcube*`) ;
`AXIORHUB_ROUNDCUBE_ROOT` permet de l'imposer.

Pour publier l'interface sur un sous-domaine dédié avec HTTPS, utilisez le modèle `deploy/apache-agent.example.conf`
puis `sudo certbot --apache -d agent.votre-cabinet.fr --redirect`.

## 4. Démarrer prudemment

1. Mode **observation** : AxiorHub Pilote analyse sans rien écrire. Contrôlez les rattachements aux dossiers.
2. Mode **brouillons** : `sudo axiorhub-mail mode drafts`. Les brouillons apparaissent dans la messagerie.
3. Activez les automatismes un par un dans *Paramètres → Automatismes*.

## 5. Données et sauvegarde

Sauvegardez `/etc/axiorhub-mail-agent/` et `/var/lib/axiorhub-mail-agent/` (chiffrés, hors du serveur). Ils contiennent
des données de clients et des secrets.
