# Rhythmbox-AlbumArtist-Filter
This plugin replaces the "Artist" filter with an "Album Artist" filter.

# Features
- Browse by Album Artist instead of track Artist.
- Follows Rhythmbox browser-layout preference changes seamlessly.
- Works seamlessly with the genre filter column that precedes the Album Artist column and the Albums filter that follows the column.

For a **user-level installation**, copy the plugin folder into:

`~/.local/share/rhythmbox/plugins/`

For a **system-wide installation**, the location depends on your distribution:

- **Arch Linux:** `/usr/lib/rhythmbox/plugins/`
- **Debian/Ubuntu/Mint (x86_64):** `/usr/lib/x86_64-linux-gnu/rhythmbox/plugins/`
- **Debian/Ubuntu/Mint (ARM64):** `/usr/lib/aarch64-linux-gnu/rhythmbox/plugins/`

For **Flatpak**, use:

`~/.var/app/org.gnome.Rhythmbox3/data/rhythmbox/plugins/`

Restart Rhythmbox, open **Preferences → Plugins**, and enable the plugin.

# Full Disclosure
  I am not a developer and am not formally trained in computer programming. AI was used to assist in the writing of the code based on my specifications, testing, and feedback. I have tested various development versions on Arch Linux with Rhythmbox 3.5.1. I have not tested the plugin on other distributions. Reviews and improvements to the code base, as well as additional feature requests, are welcome.

## Please Note
  The plugin is python-based. The issues regarding the breaking of the python-based Rhythmbox plugins was well known, and they were broken in the past for a significant amount of time. Those upstream issues appear to have been rectified now. However, future changes of  Rhythmbox or its dependencies could cause changes that affect this plugin, particularly if those changes break python-loader. Hopefully this will not happen, but if it does, this plugin could break.
