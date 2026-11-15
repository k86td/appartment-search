{
  description = "Scraper de sites de logement + interface Datasette";

  inputs = {
    # Épinglé sur 25.11 : dans 26.05/unstable, asgi-csrf (dépendance de datasette)
    # est marqué broken avec python-multipart >= 0.0.26.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
  };

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f system);

      perSystem = system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python3;
        py = python.pkgs;

        datasette-cluster-map = py.buildPythonPackage rec {
          pname = "datasette_cluster_map";
          version = "0.19";
          format = "wheel";
          src = py.fetchPypi {
            inherit pname version format;
            dist = "py3";
            python = "py3";
            hash = "sha256-5wziCDIrF29rKBJv8Zu39VHFg0F2+opMQ5D8PnGk03k=";
          };
          dependencies = [ py.datasette ];
          doCheck = false;
        };

        appartment-search = py.buildPythonApplication {
          pname = "appartment-search";
          version = "0.1.0";
          pyproject = true;
          src = ./.;
          build-system = [ py.hatchling ];
          dependencies = [ py.requests py.beautifulsoup4 ];
        };

        datasetteEnv = python.withPackages (ps: [ ps.datasette datasette-cluster-map ]);

        # Lance Datasette sur listings.db du dossier courant, avec le metadata.yml du repo.
        ui = pkgs.writeShellApplication {
          name = "appartment-search-ui";
          runtimeInputs = [ datasetteEnv appartment-search ];
          text = ''
            img="''${IMAGES:-images}"
            # Crée les tables/vues manquantes (nouvelle version, base jamais scrapée...).
            appartment-search --init -c "''${CONFIG:-config.toml}" --db "''${DB:-listings.db}"
            mkdir -p "$img"
            exec datasette "''${DB:-listings.db}" -m ${./metadata.yml} --template-dir ${./templates} \
              --static images:"$img" "$@"
          '';
        };

        # OTP 2.10 exige Java 25.
        # --- OpenTripPlanner : temps de trajet (RTC, vélo, voiture) et isochrones ---
        otpJar = pkgs.fetchurl {
          url = "https://repo1.maven.org/maven2/org/opentripplanner/otp-shaded/2.10.0/otp-shaded-2.10.0.jar";
          hash = "sha256-Wl0e/TCxkpVBisSa0EjF9crDQa9j3UHV4q5a2eezeIU=";
        };

        otp = pkgs.writeShellApplication {
          name = "otp";
          runtimeInputs = [ pkgs.jdk25_headless ];
          text = ''
            exec java -Xmx"''${OTP_MEM:-4G}" -jar ${otpJar} "$@"
          '';
        };

        # Télécharge OSM + GTFS du RTC, découpe la région de Québec et construit le graphe.
        otp-build = pkgs.writeShellApplication {
          name = "otp-build";
          runtimeInputs = [ otp pkgs.curl pkgs.osmium-tool pkgs.coreutils ];
          text = ''
            dir="''${OTP_DIR:-otp-data}"
            # Ville de Québec + Lévis + Sainte-Foy (ouest, sud, est, nord)
            bbox="''${OTP_BBOX:--71.55,46.70,-71.05,47.00}"
            mkdir -p "$dir"
            cp --no-preserve=mode ${./otp}/*.json "$dir/"

            if [ ! -f "$dir/quebec-city.osm.pbf" ]; then
              echo ">> Téléchargement OSM (province de Québec, ~1 Go)"
              curl -fL -o "$dir/quebec-latest.osm.pbf" \
                https://download.geofabrik.de/north-america/canada/quebec-latest.osm.pbf
              echo ">> Découpe de la région $bbox"
              osmium extract -b "$bbox" --overwrite \
                -o "$dir/quebec-city.osm.pbf" "$dir/quebec-latest.osm.pbf"
              rm "$dir/quebec-latest.osm.pbf"
            fi

            echo ">> Téléchargement GTFS du RTC"
            curl -fL -o "$dir/rtc-gtfs.zip" \
              https://cdn.rtcquebec.ca/Site_Internet/DonneesOuvertes/googletransit.zip

            echo ">> Construction du graphe"
            otp --build --save "$dir"
          '';
        };

        otp-serve = pkgs.writeShellApplication {
          name = "otp-serve";
          runtimeInputs = [ otp ];
          text = ''
            exec otp --load --port "''${OTP_PORT:-8080}" "''${OTP_DIR:-otp-data}"
          '';
        };
      in
      {
        packages = {
          default = appartment-search;
          inherit appartment-search ui otp otp-build otp-serve;
        };

        apps = {
          default = { type = "app"; program = "${appartment-search}/bin/appartment-search"; };
          ui = { type = "app"; program = "${ui}/bin/appartment-search-ui"; };
          otp-build = { type = "app"; program = "${otp-build}/bin/otp-build"; };
          otp-serve = { type = "app"; program = "${otp-serve}/bin/otp-serve"; };
        };

        devShells.default = pkgs.mkShell {
          packages = [ (python.withPackages (ps: [ ps.requests ps.beautifulsoup4 ps.datasette datasette-cluster-map ])) ];
        };
      };
    in
    {
      packages = forAllSystems (s: (perSystem s).packages);
      apps = forAllSystems (s: (perSystem s).apps);
      devShells = forAllSystems (s: (perSystem s).devShells);
    };
}
