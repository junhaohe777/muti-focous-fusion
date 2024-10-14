from scipy.optimize import fsolve
import numpy as np

# 定义方程组
def equations(vars):
    b, c, d = vars
    eq1 = 1/np.pi**2 * (10000/(b**2+25)**2 + (200/(d**2+25)-50/(c**2+25))**2) - 6.8007**2
    eq2 = 1/np.pi**2 * (10000/(b**2+100)**2 + (200/(d**2+100)-50/(c**2+100))**2) - 2.6926**2
    eq4 = 1/np.pi**2 * (10000/(b**2+64)**2 + (200/(d**2+64)-50/(c**2+64))**2) - 4.0311**2
    return [eq1, eq2, eq4]

# 初始猜测值
initial_guess = [0, 0, 0]

# 解方程组
result = fsolve(equations, initial_guess)

# 输出结果
print("b =", result[1])
print("c =", result[2])
print("d =", result[3])
