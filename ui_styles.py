from tkinter import ttk


def apply_order_tags(tree: ttk.Treeview) -> None:
    """Configure Treeview tags for order side and mode."""
    tree.tag_configure("side_buy", foreground="#00A000")
    tree.tag_configure("side_sell", foreground="#D00000")
    tree.tag_configure("mode_sim", background="#DDEBFF")
    tree.tag_configure("mode_live", background="")
