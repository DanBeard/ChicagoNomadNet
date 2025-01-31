from markdownify import MarkdownConverter, chomp
import posixpath

    
class MicronConverter(MarkdownConverter):
    current_path = "/" # for relative href rewriting
    reader_path = "/page/zr.mu" # so we can create valid micron links that will actually point where we want them to
    url_suffix=""
    
    def convert_a(self, el, text, convert_as_inline):
        prefix, suffix, text = chomp(text)
        if not text:
            return ''
        href = el.get('href')
        title = el.get('title')
        # For the replacement see #29: text nodes underscores are escaped
        if (self.options['autolinks']
                and text.replace(r'\_', '_') == href
                and not title
                and not self.options['default_title']):
            # Shortcut syntax
            return '`[%s]' % href
        if self.options['default_title'] and not title:
            title = href
        #title_part = ' "%s"' % title.replace('"', r'\"') if title else ''
        micron_link = self.rewrite_link(href)
        return '`F44a%s`[%s`%s]%s`f' % (prefix, text, micron_link, suffix) if href else text
    
    
    def rewrite_link(self, link):
        """
        Rewrite a link so it actually goes where we want
        """
        #TODO if the HTML has a nomadnet link? how can we detect that? hmmm
        if link is None or len(link)==0:
            return ''
        
        # nothing we can do with a link like this out to HTTP
        if link.startswith("http"):
            return link
        
        
        # absolute path
        if link.startswith("/"):
            return ":" + self.reader_path + "`p=" +link + self.url_suffix
        
        #relative path
        current_dir = posixpath.dirname(self.current_path) if not self.current_path.endswith("/") else self.current_path
        new_link = posixpath.normpath(posixpath.join(current_dir, link))
        if link.endswith("/") and not new_link.endswith("/"):
            new_link += "/"
            
        return ":" + self.reader_path + "`p=" + new_link + self.url_suffix
    
    def convert_b(self, el, text, convert_as_inline):
        return "`!" + text + "`!"
    
    def convert_em(self, el, text, convert_as_inline):
        return "`*"+text+"`*"
    
    def _convert_hn(self, n, el, text, convert_as_inline):
        """ Method name prefixed with _ to prevent <hn> to call this """
        if convert_as_inline:
            return text

        # workaround since links in headers aren't clickable right now.
        href_workaround = any(x.name == "a" for x in el.children)
        prefix = "    \n" if href_workaround else ""
        # prevent MemoryErrors in case of very large n
        n = max(1, min(8, n))
        return '\n>'*n + prefix + text + "\n"
    
    def convert_hr(self, el, text, convert_as_inline):
        return '\n-\n'
    
    def convert_img(self, el, text, convert_as_inline):
        alt = el.attrs.get('alt', None) or ''
        src = el.attrs.get('src', None) or ''
        title = el.attrs.get('title', None) or ''

        new_src = self.rewrite_link(src)
        if len(title) > 0:
            label = title + (f"〚alt:{alt}〛" if len(alt) > 0 else '')
        elif len(alt) > 0:
            label = alt
        else:
            label = src
        return '`F44a`[(🖻:%s)`%s]`f' % (label , new_src) 


    convert_i = convert_em
    
    def convert_soup(self, soup):
        self._clean_soup(soup)
        return super().convert_soup(soup)
        
    def _clean_soup(self, soup):
        # References and external links for some articles can be quite long and will be on their own seperate page isolated from the main article content. 
        bad_classes = { 
            "sidebar-list"
            "reflist",
            "references",
            "mw-references-wrap",
            "mw-reference-columns",
            "navbox",
            "infobox",
            "sidebar",
            "hatnote",
            "external",
        }
        
        for ele in soup.find_all(class_=lambda c: c in bad_classes):
            ele.decompose()
            
        related = soup.find_all(attrs={"aria-labelledby": "Links_to_related_articles"})

        for r in related:
            r.decompose()

        for tag in soup(["script", "style", "sup"]):
            tag.decompose()
    
# the good stuff here
def html_to_micron(html, current_path=None, extra_get_params=None):
    converter = MicronConverter(wrap=False, wrap_width=180, escape_underscore=False)
    # set the current path for href rewriting
    if current_path is not None:
        converter.current_path = current_path
    if extra_get_params is not None:
        if "L" not in extra_get_params:
            extra_get_params["L"] = current_path # set the last path for "back" funcationality
        converter.url_suffix = "|" + "|".join(f"{k}={v}" for k,v in extra_get_params.items())
        
    # just remove literal `, escaping is broken`
    result = converter.convert(html.replace("`","")) or ""
    return result.strip(" \n\r").replace("\n\n\n", "\n").replace("\n\n\n", "\n").strip("<|>#-") # clean up lots of empty \n from html