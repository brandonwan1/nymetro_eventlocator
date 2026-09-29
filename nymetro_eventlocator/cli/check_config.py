from __future__ import annotations

NAME = "check-config"
NEEDS_CONFIG = True

# source -> (settings key holding its secret, the ${VAR} name to set it with)
SOURCE_SECRETS = {
    "ticketmaster": ("api_key", "TICKETMASTER_API_KEY"),
    "eventbrite": ("token", "EVENTBRITE_TOKEN"),
}


def add_parser(sub) -> None:
    sub.add_parser(NAME, help="validate config.yaml and exit")


def run(args, cfg) -> int:
    enabled = sum(1 for s in cfg.sources.values() if s.get("enabled"))
    if cfg.catch_all:
        print(f"config ok: no interests set, {enabled} sources enabled")
        print("note: no interests set: keeping every event from your enabled sources in one 'All events' section. "
              "Add interests to filter (README 'Customizing your interests').")
    else:
        print(f"config ok: {len(cfg.categories)} categories, {enabled} sources enabled")
    if not enabled:
        print("note: no sources are enabled, so runs will find nothing. Add a calendar with "
              "`nymetro_eventlocator add <link>`.")
    for name, (key, var) in SOURCE_SECRETS.items():
        s = cfg.sources.get(name)
        if s and s.get("enabled") and not s.get(key):
            print(f"note: '{name}' is enabled but {var} isn't set, so it will find nothing. "
                  f"Run `nymetro_eventlocator secrets set {var}`.")
    return 0
