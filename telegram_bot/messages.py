"""Trusted presentation values, built only from escaped application-owned templates."""


class HTMLReply(str):
    """Complete HTML fragments; never split a tag or link during Telegram delivery.

    Only renderers that escape every external field may create this type.
    Ordinary user/model strings remain plain text, even if they contain HTML.
    """
    def __new__(cls,parts):
        parts=tuple(parts)
        if not 1<=len(parts)<=6 or any(not isinstance(p,str) or not p or len(p.encode('utf-8'))>3500 for p in parts):
            raise ValueError('Invalid bounded HTML reply')
        instance=super().__new__(cls,'\n\n'.join(parts))
        instance.parts=parts
        return instance
