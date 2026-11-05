









_G.menu = menu.new({}, { title = "Luaxite", log_percent = 40 })

local requires = {
    "bot"
}

for _, v in ipairs(requires) do
    require("./"..v)
end