# Module NixOS : scraping périodique, interface Datasette, OpenTripPlanner et
# (optionnellement) exposition sur le tailnet avec `tailscale serve`.
#
#   imports = [ appartment-search.nixosModules.default ];
#   services.appartment-search = {
#     enable = true;
#     settings.trajets.destinations = [ { nom = "ULaval"; lat = 46.77866; lng = -71.27458; } ];
#     tailscale.enable = true;
#   };
self:
{ config, lib, pkgs, ... }:

let
  cfg = config.services.appartment-search;
  pkgsFlake = self.packages.${pkgs.stdenv.hostPlatform.system};
  toml = pkgs.formats.toml { };

  # config.toml du dépôt, avec les réglages de l'utilisateur par-dessus. Les chemins
  # (base, photos, OTP) sont imposés par le module.
  settings = lib.recursiveUpdate
    (lib.recursiveUpdate (builtins.fromTOML (builtins.readFile ../config.toml)) cfg.settings)
    {
      database = "${cfg.dataDir}/listings.db";
      images.dossier = "${cfg.dataDir}/images";
      trajets.otp_url = "http://127.0.0.1:${toString cfg.otp.port}/otp/gtfs/v1";
    };
  # Avec le worker, le scraping ne calcule plus de trajets lui-même.
  settingsScrape = lib.recursiveUpdate settings
    (lib.optionalAttrs (cfg.otp.enable && cfg.otp.trajetsWorker) { trajets.max_par_passage = 0; });
  configFile = toml.generate "appartment-search.toml" settingsScrape;

  # Sites actifs, et répartition entre horaire propre et horaire global.
  actifs = lib.attrNames (lib.filterAttrs (_: c: c.enabled or true) (settings.scrapers or { }));
  propres = lib.filterAttrs (n: c: c.schedule != null && lib.elem n actifs) cfg.scrapers;
  globaux = lib.filter (n: !(propres ? ${n})) actifs;

  serviceScrape = description: sources: {
    inherit description;
    after = [ "network-online.target" ] ++ lib.optional cfg.otp.enable "appartment-search-otp.service";
    wants = [ "network-online.target" ];
    serviceConfig = commun // {
      Type = "oneshot";
      ExecStart = "${pkgsFlake.appartment-search}/bin/appartment-search -c ${configFile} --sources ${lib.concatStringsSep "," sources}";
      TimeoutStartSec = "2h";
    };
  };
  timer = schedule: {
    wantedBy = [ "timers.target" ];
    timerConfig = { OnCalendar = schedule; Persistent = true; RandomizedDelaySec = "10m"; };
  };

  commun = {
    User = "appartment-search";
    Group = "appartment-search";
    WorkingDirectory = cfg.dataDir;
  } // lib.optionalAttrs (cfg.dataDir == "/var/lib/appartment-search") {
    # systemd (re)donne le dossier à l'utilisateur à chaque démarrage, même s'il a été
    # remplacé par un montage (ex. impermanence) appartenant à root.
    StateDirectory = "appartment-search";
    StateDirectoryMode = "0750";
  } // {
    # Durcissement raisonnable : rien d'autre que le dossier de données en écriture.
    ProtectSystem = "strict";
    ReadWritePaths = [ cfg.dataDir ];
    ProtectHome = true;
    PrivateTmp = true;
    NoNewPrivileges = true;
  };
  envOtp = {
    OTP_DIR = "${cfg.dataDir}/otp-data";
    OTP_MEM = cfg.otp.memory;
    OTP_PORT = toString cfg.otp.port;
    OTP_OSM_MAX_AGE_DAYS = toString cfg.otp.osmMaxAgeDays;
  } // lib.optionalAttrs (cfg.otp.bbox != null) { OTP_BBOX = cfg.otp.bbox; };
in
{
  options.services.appartment-search = {
    enable = lib.mkEnableOption "la recherche d'appartements (scraping + interface)";

    dataDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/appartment-search";
      description = "Base SQLite, photos en cache et graphe OTP.";
    };

    settings = lib.mkOption {
      type = toml.type;
      default = { };
      example = lib.literalExpression ''
        {
          filtres.prix_max = 1500;
          scrapers.kijiji.max_details = 1000;
          trajets.destinations = [
            { nom = "ULaval"; lat = 46.77866; lng = -71.27458; }
            { nom = "Travail"; lat = 46.81559; lng = -71.26786; }
          ];
        }
      '';
      description = "Fusionné avec le config.toml du dépôt (les listes, comme les destinations, sont remplacées).";
    };

    schedule = lib.mkOption {
      type = lib.types.str;
      default = "*-*-* 00/6:00:00";
      example = "hourly";
      description = ''
        Fréquence du scraping des sites sans horaire propre, au format OnCalendar de systemd
        (`hourly`, `daily`, `weekly`, `monthly`, `*-*-* 00/6:00:00`…, voir systemd.time(7)).
        Vérifier une expression : `systemd-analyze calendar "…"`.
      '';
    };

    scrapers = lib.mkOption {
      type = lib.types.attrsOf (lib.types.submodule {
        options.schedule = lib.mkOption {
          type = lib.types.nullOr lib.types.str;
          default = null;
          description = "Horaire propre à ce site (format OnCalendar). null = horaire global.";
        };
      });
      default = { };
      example = lib.literalExpression ''
        {
          kijiji.schedule = "hourly";
          msi.schedule = "daily";
        }
      '';
      description = ''
        Horaire par site. Chaque site configuré a son propre timer
        (appartment-search-scrape-<site>) ; les étapes communes (dédup, photos, trajets)
        s'exécutent à chaque passage. Deux passages simultanés s'attendent (verrou).
      '';
    };

    ui = {
      enable = lib.mkOption { type = lib.types.bool; default = true; description = "Interface Datasette (/carte)."; };
      port = lib.mkOption { type = lib.types.port; default = 8001; };
      host = lib.mkOption {
        type = lib.types.str;
        default = "127.0.0.1";
        description = "Adresse d'écoute. Garder 127.0.0.1 avec Tailscale ; l'interface n'a pas d'authentification.";
      };
    };

    otp = {
      enable = lib.mkOption { type = lib.types.bool; default = true; description = "OpenTripPlanner (temps de trajet, isochrones)."; };
      port = lib.mkOption { type = lib.types.port; default = 8080; };
      memory = lib.mkOption { type = lib.types.str; default = "4G"; description = "Mémoire Java (-Xmx)."; };
      bbox = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        example = "-71.65,46.65,-70.85,47.15";
        description = "Zone de la carte (ouest,sud,est,nord). Défaut : celle du script otp-build.";
      };
      rebuildSchedule = lib.mkOption {
        type = lib.types.str;
        default = "monthly";
        description = "Reconstruction du graphe avec les derniers horaires du RTC (format OnCalendar).";
      };
      trajetsWorker = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = ''
          Service en continu qui calcule tous les temps de trajet manquants dès qu'il y en a
          (au lieu d'un nombre limité à chaque passage de scraping).
        '';
      };
      osmMaxAgeDays = lib.mkOption {
        type = lib.types.ints.positive;
        default = 90;
        description = "À la reconstruction, la carte OpenStreetMap est retéléchargée si elle a plus de N jours.";
      };
    };

    tailscale.enable = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Expose l'interface en HTTPS sur ton tailnet (tailscale serve), visible de tes appareils seulement.";
    };
  };

  config = lib.mkIf cfg.enable (lib.mkMerge [ {
    users.users.appartment-search = { isSystemUser = true; group = "appartment-search"; home = cfg.dataDir; };
    users.groups.appartment-search = { };
    systemd.tmpfiles.rules = [ "d ${cfg.dataDir} 0750 appartment-search appartment-search -" ];

    # Isochrones : longues, à lancer à la main (systemctl start appartment-search-isochrones).
    systemd.services.appartment-search-isochrones = lib.mkIf cfg.otp.enable {
      description = "Recherche d'appartements : calcul des isochrones";
      after = [ "appartment-search-otp.service" ];
      requires = [ "appartment-search-otp.service" ];
      serviceConfig = commun // {
        Type = "oneshot";
        ExecStart = "${pkgsFlake.appartment-search}/bin/appartment-search -c ${configFile} --isochrones";
        TimeoutStartSec = "infinity";
      };
    };

    # ---------- Temps de trajet en continu ----------
    systemd.services.appartment-search-trajets = lib.mkIf (cfg.otp.enable && cfg.otp.trajetsWorker) {
      description = "Recherche d'appartements : calcul des temps de trajet en continu";
      wantedBy = [ "multi-user.target" ];
      after = [ "appartment-search-otp.service" ];
      wants = [ "appartment-search-otp.service" ];
      serviceConfig = commun // {
        ExecStart = "${pkgsFlake.appartment-search}/bin/appartment-search -c ${toml.generate "appartment-search-trajets.toml" settings} --trajets-worker";
        Restart = "always";
        RestartSec = "30s";
        Nice = 10;
      };
    };

    # ---------- Interface ----------
    systemd.services.appartment-search-ui = lib.mkIf cfg.ui.enable {
      description = "Recherche d'appartements : interface Datasette";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];
      environment = { DB = "${cfg.dataDir}/listings.db"; IMAGES = "${cfg.dataDir}/images"; CONFIG = "${configFile}"; };
      serviceConfig = commun // {
        ExecStart = "${pkgsFlake.ui}/bin/appartment-search-ui -h ${cfg.ui.host} -p ${toString cfg.ui.port}";
        Restart = "on-failure";
      };
    };

    # ---------- OpenTripPlanner ----------
    systemd.services.appartment-search-otp-build = lib.mkIf cfg.otp.enable {
      description = "OpenTripPlanner : téléchargement OSM + GTFS RTC et construction du graphe";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      environment = envOtp;
      serviceConfig = commun // {
        Type = "oneshot";
        ExecStart = "${pkgsFlake.otp-build}/bin/otp-build";
        # Le service OTP recharge le nouveau graphe.
        ExecStartPost = "+${pkgs.systemd}/bin/systemctl try-restart appartment-search-otp.service";
        TimeoutStartSec = "2h";
      };
    };
    systemd.timers.appartment-search-otp-build = lib.mkIf cfg.otp.enable {
      wantedBy = [ "timers.target" ];
      timerConfig = { OnCalendar = cfg.otp.rebuildSchedule; Persistent = true; };
    };
    systemd.services.appartment-search-otp = lib.mkIf cfg.otp.enable {
      description = "OpenTripPlanner : serveur";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];
      environment = envOtp;
      # Pas de graphe encore : on le construit d'abord (première installation).
      preStart = ''
        if [ ! -f "${cfg.dataDir}/otp-data/graph.obj" ]; then
          ${pkgsFlake.otp-build}/bin/otp-build
        fi
      '';
      serviceConfig = commun // {
        ExecStart = "${pkgsFlake.otp-serve}/bin/otp-serve";
        Restart = "on-failure";
        TimeoutStartSec = "2h";
      };
    };

    # ---------- Tailscale ----------
    systemd.services.appartment-search-tailscale = lib.mkIf (cfg.tailscale.enable && cfg.ui.enable) {
      description = "Expose l'interface sur le tailnet (tailscale serve)";
      wantedBy = [ "multi-user.target" ];
      after = [ "tailscaled.service" "appartment-search-ui.service" ];
      wants = [ "tailscaled.service" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${config.services.tailscale.package}/bin/tailscale serve --bg --https=443 http://127.0.0.1:${toString cfg.ui.port}";
        ExecStop = "${config.services.tailscale.package}/bin/tailscale serve --https=443 off";
      };
    };
  }

  {
    # ---------- Scraping périodique ----------
    # Un service + timer global pour les sites sans horaire propre…
    systemd.services = lib.mkMerge [
      (lib.mkIf (globaux != [ ]) {
        appartment-search-scrape = serviceScrape
          "Recherche d'appartements : scraping (${lib.concatStringsSep ", " globaux})" globaux;
      })
      # … et un par site qui a son propre horaire.
      (lib.mapAttrs' (n: _: lib.nameValuePair "appartment-search-scrape-${n}"
        (serviceScrape "Recherche d'appartements : scraping ${n}" [ n ])) propres)
    ];
    systemd.timers = lib.mkMerge [
      (lib.mkIf (globaux != [ ]) { appartment-search-scrape = timer cfg.schedule; })
      (lib.mapAttrs' (n: c: lib.nameValuePair "appartment-search-scrape-${n}" (timer c.schedule)) propres)
    ];

  } ]);
}
