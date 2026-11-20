"""
Starting data of the LOV lists COUNTRY -> STATE -> CITY (services/lov.py seeds it).

India: all 28 states and 8 union territories, each with its main cities. This is
not every town in India: admins add more cities (or countries and their states)
in Catalog > Master lists, and the seed never removes or overwrites them.

Codes: country "IN", state "IN-" + a short state code ("IN-MH"),
city = "<state code>/<city name>" (two states can have a city with the same name).
Aliases are other names the address lookup (geo.py / OpenStreetMap) may return,
e.g. "Bangalore" for Bengaluru, or the district's name where OpenStreetMap knows no town
("Gautam Buddha Nagar" -> Noida); the survey form uses them to pick the right value.
"""

COUNTRIES = [("IN", "India", ["Bharat"])]

# (state code, state name, aliases, [city or (city, [aliases]), ...])
INDIA = [
    ("IN-AP", "Andhra Pradesh", [], [("Visakhapatnam", ["Vizag"]), "Vijayawada", "Guntur", "Nellore", "Kurnool", "Tirupati",
                                     "Kakinada", ("Rajamahendravaram", ["Rajahmundry"]), "Kadapa", "Anantapur"]),
    ("IN-AR", "Arunachal Pradesh", [], ["Itanagar", "Naharlagun", "Pasighat", "Tawang", "Ziro", "Bomdila"]),
    ("IN-AS", "Assam", [], [("Guwahati", ["Kamrup Metropolitan"]), "Dibrugarh", "Silchar", "Jorhat", "Tezpur", "Nagaon", "Tinsukia", "Bongaigaon"]),
    ("IN-BR", "Bihar", [], ["Patna", "Gaya", "Bhagalpur", "Muzaffarpur", "Darbhanga", "Purnia", "Arrah", "Begusarai", "Katihar", "Munger"]),
    ("IN-CT", "Chhattisgarh", [], ["Raipur", "Bhilai", "Bilaspur", "Korba", "Durg", "Rajnandgaon", "Raigarh", "Jagdalpur"]),
    ("IN-GA", "Goa", [], ["Panaji", ("Margao", ["Madgaon"]), "Vasco da Gama", "Mapusa", "Ponda"]),
    ("IN-GJ", "Gujarat", [], ["Ahmedabad", "Surat", ("Vadodara", ["Baroda"]), "Rajkot", "Bhavnagar", "Jamnagar", "Gandhinagar",
                              "Junagadh", "Anand", "Bharuch", "Navsari", "Morbi"]),
    ("IN-HR", "Haryana", [], [("Gurugram", ["Gurgaon"]), "Faridabad", "Panipat", "Ambala", "Hisar", "Karnal", "Rohtak", "Sonipat",
                              "Yamunanagar", "Panchkula"]),
    ("IN-HP", "Himachal Pradesh", [], ["Shimla", "Dharamshala", "Solan", "Mandi", "Kullu", "Manali", "Hamirpur", "Una"]),
    ("IN-JH", "Jharkhand", [], ["Ranchi", "Jamshedpur", "Dhanbad", ("Bokaro Steel City", ["Bokaro"]), "Hazaribagh", "Deoghar", "Giridih"]),
    ("IN-KA", "Karnataka", [], [("Bengaluru", ["Bangalore"]), ("Mysuru", ["Mysore"]), ("Mangaluru", ["Mangalore"]), ("Hubballi", ["Hubli"]),
                                "Dharwad", ("Belagavi", ["Belgaum"]), ("Kalaburagi", ["Gulbarga"]), ("Ballari", ["Bellary"]),
                                "Davanagere", ("Shivamogga", ["Shimoga"]), ("Tumakuru", ["Tumkur"]), "Udupi"]),
    ("IN-KL", "Kerala", [], [("Thiruvananthapuram", ["Trivandrum"]), ("Kochi", ["Cochin", "Ernakulam"]), ("Kozhikode", ["Calicut"]),
                             ("Thrissur", ["Trichur"]), ("Kollam", ["Quilon"]), ("Kannur", ["Cannanore"]), ("Alappuzha", ["Alleppey"]),
                             ("Palakkad", ["Palghat"]), "Kottayam", "Malappuram"]),
    ("IN-MP", "Madhya Pradesh", [], ["Bhopal", "Indore", "Jabalpur", "Gwalior", "Ujjain", "Sagar", "Rewa", "Satna", "Ratlam", "Dewas", "Chhindwara"]),
    ("IN-MH", "Maharashtra", [], [("Mumbai", ["Bombay"]), ("Pune", ["Poona"]), "Nagpur", "Nashik", "Thane", "Navi Mumbai",
                                  ("Chhatrapati Sambhajinagar", ["Aurangabad"]), "Solapur", "Kolhapur", "Amravati", "Nanded", "Sangli",
                                  "Akola", "Jalgaon", ("Ahilyanagar", ["Ahmednagar"])]),
    ("IN-MN", "Manipur", [], ["Imphal", "Thoubal", "Churachandpur", "Bishnupur"]),
    ("IN-ML", "Meghalaya", [], ["Shillong", "Tura", "Jowai"]),
    ("IN-MZ", "Mizoram", [], ["Aizawl", "Lunglei", "Champhai"]),
    ("IN-NL", "Nagaland", [], ["Kohima", "Dimapur", "Mokokchung"]),
    ("IN-OR", "Odisha", ["Orissa"], ["Bhubaneswar", "Cuttack", "Rourkela", ("Berhampur", ["Brahmapur"]), "Sambalpur", "Puri",
                                    ("Balasore", ["Baleshwar"]), "Bhadrak"]),
    ("IN-PB", "Punjab", [], ["Ludhiana", "Amritsar", "Jalandhar", "Patiala", "Bathinda", ("Mohali", ["Sahibzada Ajit Singh Nagar"]),
                             "Pathankot", "Hoshiarpur", "Moga"]),
    ("IN-RJ", "Rajasthan", [], ["Jaipur", "Jodhpur", "Udaipur", "Kota", "Ajmer", "Bikaner", "Bhilwara", "Alwar", "Sikar", "Bharatpur"]),
    ("IN-SK", "Sikkim", [], ["Gangtok", "Namchi", ("Gyalshing", ["Geyzing"]), "Mangan"]),
    ("IN-TN", "Tamil Nadu", [], [("Chennai", ["Madras"]), "Coimbatore", "Madurai", ("Tiruchirappalli", ["Trichy"]), "Salem", "Tiruppur",
                                 "Erode", "Vellore", ("Tirunelveli", ["Nellai"]), ("Thoothukudi", ["Tuticorin"]), ("Thanjavur", ["Tanjore"]),
                                 "Hosur"]),
    ("IN-TG", "Telangana", [], ["Hyderabad", "Secunderabad", "Warangal", "Nizamabad", "Karimnagar", "Khammam", "Mahbubnagar", "Nalgonda"]),
    ("IN-TR", "Tripura", [], ["Agartala", "Udaipur", "Dharmanagar", "Kailashahar"]),
    ("IN-UP", "Uttar Pradesh", [], ["Lucknow", "Kanpur", "Ghaziabad", "Agra", ("Varanasi", ["Banaras", "Benares"]), "Meerut",
                                    ("Prayagraj", ["Allahabad"]), ("Noida", ["Gautam Buddha Nagar"]), "Greater Noida", "Bareilly", "Aligarh", "Moradabad", "Gorakhpur", "Jhansi",
                                    "Mathura", ("Ayodhya", ["Faizabad"]), "Saharanpur"]),
    ("IN-UT", "Uttarakhand", ["Uttaranchal"], ["Dehradun", "Haridwar", "Roorkee", "Haldwani", "Rudrapur", "Rishikesh", "Kashipur", "Nainital"]),
    ("IN-WB", "West Bengal", [], [("Kolkata", ["Calcutta"]), "Howrah", "Durgapur", "Asansol", "Siliguri", ("Bardhaman", ["Burdwan"]),
                                  "Kharagpur", "Haldia", ("Malda", ["English Bazar"]), "Darjeeling"]),
    # Union territories
    ("IN-AN", "Andaman and Nicobar Islands", [], [("Sri Vijaya Puram", ["Port Blair"])]),
    ("IN-CH", "Chandigarh", [], ["Chandigarh"]),
    ("IN-DH", "Dadra and Nagar Haveli and Daman and Diu", [], ["Daman", "Diu", "Silvassa"]),
    ("IN-DL", "Delhi", ["National Capital Territory of Delhi", "NCT of Delhi", "New Delhi"], ["New Delhi", "Delhi"]),
    ("IN-JK", "Jammu and Kashmir", [], ["Srinagar", "Jammu", "Anantnag", "Baramulla", "Udhampur", "Kathua"]),
    ("IN-LA", "Ladakh", [], ["Leh", "Kargil"]),
    ("IN-LD", "Lakshadweep", [], ["Kavaratti", "Agatti"]),
    ("IN-PY", "Puducherry", ["Pondicherry"], [("Puducherry", ["Pondicherry"]), "Karaikal", "Mahe", "Yanam"]),
]


def city_entries(cities):
    """[(name, aliases)] from the mixed list above."""
    return [(c, []) if isinstance(c, str) else (c[0], list(c[1])) for c in cities]
