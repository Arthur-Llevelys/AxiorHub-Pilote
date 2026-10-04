/* Open the cabinet agent from both standalone Roundcube and an embedded webmail. */
(function () {
    'use strict';

    if (typeof rcmail === 'undefined') {
        return;
    }

    rcmail.addEventListener('init', function () {
        rcmail.register_command('plugin.axiorhub_mail_agent_open', function () {
            var url = rcmail.env.axiorhub_mail_agent_url;
            if (typeof url !== 'string' || !/^https:\/\/agent\.example\.com\/agent-courriel\//.test(url)) {
                rcmail.display_message('Adresse de l’agent IA indisponible.', 'error');
                return false;
            }
            var opened = window.open(url, '_blank', 'noopener,noreferrer');
            if (opened) {
                opened.opener = null;
            }
            // noopener can return null even when the tab opened successfully.
            // Never navigate the embedded Roundcube frame to the protected agent.
            return false;
        }, true);
    });
}());
